"""Carpeta vigilada: importa sola los Excel que se dejan en la carpeta «importar».

Funcionamiento (un hilo en segundo plano, arrancado desde servidor.py):
  1. Cada pocos segundos mira la carpeta. Un archivo se procesa cuando lleva un rato sin cambiar
     de tamaño y nadie lo tiene abierto (así no se lee una descarga a medias).
  2. Lo analiza con la misma lógica que la importación manual (api.analyse_upload).
  3. Si todo está claro lo importa y lo mueve a «importar/procesados».
     Si hay algo que debe ver una persona (filas con error, locales nuevos, formato desconocido…)
     NO importa nada y lo mueve a «importar/revisar»; desde ahí se sube a mano en «Importar».
  4. Cada archivo queda registrado en la tabla watch_files (se ve en la pantalla Importar).

Un mismo archivo (mismo contenido) solo se importa una vez aunque se deje varias veces.

Variables de entorno:
  WATCH=0          desactiva la carpeta vigilada
  WATCH_DIR=ruta   otra carpeta (por defecto «importar» junto a la app)
"""
import hashlib
import os
import shutil
import threading
import time
from datetime import datetime

from flask import g

import api
import db
import importer
import services as svc

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
POLL_SECONDS = 10
SETTLE_SECONDS = 5           # el archivo debe llevar este tiempo sin modificarse
MAX_BYTES = 10 * 1024 * 1024  # mismo límite que la subida manual
DONE_DIR, REVIEW_DIR = "procesados", "revisar"
# Descargas a medias de los navegadores y archivos temporales de Office: se ignoran
_PARTIAL_EXT = (".crdownload", ".part", ".partial", ".tmp", ".download")

STATE = {"last_scan": None, "last_error": None}
_lock = threading.Lock()
_wake = threading.Event()
_worker = None
_seen = {}  # ruta -> (tamaño, fecha de modificación) en la pasada anterior


def enabled():
    return os.environ.get("WATCH", "1") != "0"


def folder():
    return os.path.abspath(os.environ.get("WATCH_DIR") or os.path.join(BASE_DIR, "importar"))


def start(app):
    """Crea las carpetas y arranca el hilo (una sola vez)."""
    global _worker
    if not enabled():
        return
    base = folder()
    for sub in ("", DONE_DIR, REVIEW_DIR):
        os.makedirs(os.path.join(base, sub), exist_ok=True)
    with _lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run, args=(app,), daemon=True, name="carpeta-vigilada")
            _worker.start()


def running():
    return _worker is not None and _worker.is_alive()


def scan_now():
    _wake.set()


def _run(app):
    while True:
        try:
            with app.app_context():
                scan()
            STATE["last_error"] = None
        except Exception as exc:  # el hilo no debe morir nunca
            app.logger.exception("Error en la carpeta vigilada")
            STATE["last_error"] = f"Error inesperado: {exc}"
        STATE["last_scan"] = svc.now_str()
        _wake.wait(timeout=POLL_SECONDS)
        _wake.clear()


# --------------------------------------------------------------------------- #
# Una pasada por la carpeta
# --------------------------------------------------------------------------- #
def _candidates(base):
    for name in sorted(os.listdir(base)):
        path = os.path.join(base, name)
        low = name.lower()
        if not os.path.isfile(path) or name.startswith(("~$", ".")) or low.endswith(_PARTIAL_EXT) \
                or low in ("desktop.ini", "thumbs.db"):
            continue
        yield name, path


def _ready(path):
    """True si el archivo ha terminado de escribirse: mismo tamaño que en la pasada anterior,
    sin cambios desde hace unos segundos y sin que otro programa (Excel, OneDrive…) lo tenga bloqueado."""
    try:
        st = os.stat(path)
    except OSError:
        return False
    sig, prev = (st.st_size, st.st_mtime), _seen.get(path)
    _seen[path] = sig
    if prev != sig or time.time() - st.st_mtime < SETTLE_SECONDS:
        return False
    try:
        with open(path, "r+b"):  # en Windows falla si otro programa lo tiene abierto
            pass
    except OSError:
        return False
    return True


def pending_files():
    base = folder()
    if not os.path.isdir(base):
        return []
    return [name for name, _ in _candidates(base)]


def scan():
    base = folder()
    if not os.path.isdir(base):
        os.makedirs(base, exist_ok=True)
    names = list(_candidates(base))
    for gone in set(_seen) - {p for _, p in names}:
        _seen.pop(gone, None)
    for name, path in names:
        if _ready(path):
            process(path, name)
            _seen.pop(path, None)


# --------------------------------------------------------------------------- #
# Un archivo
# --------------------------------------------------------------------------- #
def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _move(path, name, sub):
    """Mueve el archivo a la subcarpeta sin pisar otro con el mismo nombre."""
    dest_dir = os.path.join(folder(), sub)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, name)
    if os.path.exists(dest):
        stem, ext = os.path.splitext(name)
        dest = os.path.join(dest_dir, f"{stem} ({datetime.now():%Y%m%d-%H%M%S}){ext}")
    shutil.move(path, dest)


def _record(name, sha, status, message, batch_id=None, created=0):
    conn = db.get_db()
    conn.execute("""INSERT INTO watch_files (filename, sha256, status, message, batch_id, created_count, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""", (name[:255], sha, status, message, batch_id, created, svc.now_str()))
    conn.commit()


def _as_admin():
    """La importación se registra a nombre del primer administrador (las incidencias necesitan un autor)."""
    row = db.get_db().execute("SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1").fetchone()
    g.user = svc.load_user(row["id"]) if row else None
    return g.user is not None


def _review_reasons(ctx, name):
    s, reasons = ctx["summary"], []
    if s["errors"]:
        reasons.append(f"{s['errors']} fila{'s' if s['errors'] != 1 else ''} con error")
    new_stores = [st["name"] for st in ctx["stores"].values() if st["is_new"]]
    if new_stores:
        reasons.append("locales nuevos que hay que confirmar: " + ", ".join(new_stores[:5]))
    if importer.PROFILES[ctx["profile"]].get("needs_date") and not importer.month_from_filename(name):
        reasons.append("el archivo no indica el día de los pedidos y el nombre no trae el mes (p. ej. «… OCT 26»)")
    return reasons


def process(path, name):
    """Analiza e importa (o aparta para revisar) un archivo. Devuelve el estado registrado."""
    if not _as_admin():
        STATE["last_error"] = "No hay ningún usuario administrador: no se puede importar."
        return None
    sha = _sha256(path)
    conn = db.get_db()

    done = conn.execute("SELECT created_at FROM watch_files WHERE sha256 = ? AND status IN ('importado', 'sin_novedades') "
                        "ORDER BY id DESC LIMIT 1", (sha,)).fetchone()
    # Se registra antes de mover: si el movimiento falla (OneDrive o Excel lo bloquean), en la
    # siguiente pasada el archivo se reconoce como ya procesado y no se importa dos veces.
    if done:
        _record(name, sha, "duplicado", f"Este mismo archivo ya se procesó el {svc.fmt_dt(done['created_at'])}.")
        _move(path, name, DONE_DIR)
        return "duplicado"

    problem = None
    ext = os.path.splitext(name)[1].lower()
    if ext not in api.ALLOWED_EXT:
        problem = f"Formato no admitido ({ext or 'sin extensión'}). Solo Excel .xlsx o .xlsm."
    elif os.path.getsize(path) > MAX_BYTES:
        problem = "El archivo supera el máximo de 10 MB."
    else:
        try:
            ctx = api.analyse_upload(path, name)
        except api.ImportProblem as exc:
            problem = str(exc)
        else:
            reasons = _review_reasons(ctx, name)
            if reasons:
                problem = "No se ha importado nada: " + "; ".join(reasons) + ". Súbelo a mano para revisarlo."
            elif not ctx["summary"]["new"]:
                _record(name, sha, "sin_novedades", f"{ctx['summary']['rows']} pedidos, todos importados antes.")
                _move(path, name, DONE_DIR)
                return "sin_novedades"

    if problem:
        _record(name, sha, "revisar", problem)
        _move(path, name, REVIEW_DIR)
        return "revisar"

    result = api.run_import(path, name, None, delete_after=False)
    msg = f"{result['created']} incidencias creadas · {svc.fmt_money(result['claimed_total'])} a reclamar"
    if result["duplicates"] or result["repeated"]:
        msg += f" · {result['duplicates'] + result['repeated']} ya existían"
    _record(name, sha, "importado", msg + ".", result["batch_id"], result["created"])
    _move(path, name, DONE_DIR)
    return "importado"


def status():
    rows = db.get_db().execute("SELECT * FROM watch_files ORDER BY id DESC LIMIT 20").fetchall()
    return {
        "enabled": enabled(), "running": running(), "folder": folder(),
        "review_folder": os.path.join(folder(), REVIEW_DIR), "poll_seconds": POLL_SECONDS,
        "last_scan": STATE["last_scan"], "last_error": STATE["last_error"],
        "waiting": pending_files(), "recent": [dict(r) for r in rows],
    }
