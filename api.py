"""API REST JSON para el frontend separado (carpeta frontend/).

Autenticación: POST /api/auth/login devuelve un token firmado; el frontend lo envía en
cada petición con la cabecera  Authorization: Bearer <token>.  Caduca a las 12 h.
"""
import glob
import json
import os
import re
import secrets
import time
from collections import OrderedDict
from datetime import date, datetime
from functools import wraps

from flask import Blueprint, Response, current_app, g, jsonify, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename

import claims
import db
import importer
import security
import services as svc
from constants import (ALERT_DAYS, INCIDENT_TYPES, OPEN_STATUSES, PLATFORMS,
                       PRIORITIES, STATUSES)

bp = Blueprint("api", __name__, url_prefix="/api")

TOKEN_MAX_AGE = 12 * 3600
ALLOWED_EXT = (".xlsx", ".xlsm")
UPLOAD_TTL = 24 * 3600
MAX_PREVIEW_ITEMS = 300


# El frontend se sirve desde el mismo servidor (/app/), así que no hace falta CORS.
@bp.after_request
def no_cache(resp):
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.before_request
def load_api_user():
    g.user = None
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        try:
            payload = _serializer().loads(header[7:], max_age=TOKEN_MAX_AGE)
            user = svc.load_user(payload.get("uid")) if isinstance(payload, dict) else None
            # El token deja de valer si la contraseña ha cambiado desde que se emitió
            if user is not None and payload.get("pv") == svc.password_version(user):
                g.user = user
        except (BadSignature, SignatureExpired):
            g.user = None


def _serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="api-auth")


def _make_token(user):
    return _serializer().dumps({"uid": user["id"], "pv": svc.password_version(user)})


def auth_required(view):
    """Exige sesión válida. Mientras no cambie la contraseña de fábrica, solo deja usar /me y /auth/password."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return error("Sesión no válida o caducada. Vuelve a iniciar sesión.", 401)
        if g.user["must_change_password"] and request.endpoint not in ("api.me", "api.change_password"):
            return jsonify({"error": "Tienes que cambiar la contraseña antes de continuar.",
                            "code": "must_change_password"}), 403
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not svc.is_admin():
            return error("Solo un administrador puede hacer esto.", 403)
        return view(*args, **kwargs)
    return wrapped


def error(message, code=400, **extra):
    return jsonify({"error": message, **extra}), code


def body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def incident_json(row):
    d = dict(row)
    d["days"] = svc.days_since(d["order_date"])
    d["is_late"] = d["status"] in OPEN_STATUSES and d["days"] > ALERT_DAYS
    try:
        d["source_data"] = json.loads(d.get("source_data") or "[]")
    except ValueError:
        d["source_data"] = []
    return d


def load_incident(iid):
    inc = svc.get_incident(iid)
    if inc is None:
        return None, error("Incidencia no encontrada.", 404)
    return inc, None


# --------------------------------------------------------------------------- #
# Autenticación y catálogos
# --------------------------------------------------------------------------- #
@bp.post("/auth/login")
def login():
    data = body()
    username = str(data.get("username", "")).strip()[:64]
    password = str(data.get("password", ""))[:256]
    ip = request.remote_addr or "?"
    wait = security.seconds_blocked(ip, username)
    if wait:
        return error(security.blocked_message(wait), 429)
    user = db.get_db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if not user or not check_password_hash(user["password_hash"], password):
        security.register_failure(ip, username)
        time.sleep(0.5)  # frena un poco los intentos por fuerza bruta
        return error("Usuario o contraseña incorrectos.", 401)
    security.register_success(ip, username)
    g.user = svc.load_user(user["id"])
    return jsonify({"token": _make_token(g.user), "user": svc.user_public(g.user)})


@bp.get("/me")
@auth_required
def me():
    return jsonify(svc.user_public(g.user))


@bp.post("/auth/password")
@auth_required
def change_password():
    data = body()
    err = svc.change_password(g.user, data.get("current_password"), data.get("new_password"), data.get("confirm_password"))
    if err:
        return error(err, 422)
    g.user = svc.load_user(g.user["id"])
    # Las sesiones abiertas en otros dispositivos quedan cerradas; esta recibe un token nuevo.
    return jsonify({"ok": True, "token": _make_token(g.user), "user": svc.user_public(g.user)})


@bp.get("/meta")
@auth_required
def meta():
    return jsonify({
        "platforms": PLATFORMS,
        "incident_types": INCIDENT_TYPES,
        "statuses": STATUSES,
        "open_statuses": list(OPEN_STATUSES),
        "priorities": PRIORITIES,
        "alert_days": ALERT_DAYS,
        "restaurants": [dict(r) for r in svc.get_restaurants()],
    })


@bp.get("/dashboard")
@auth_required
def dashboard():
    try:
        days = int(request.args.get("dias", 30))
    except ValueError:
        days = 30
    return jsonify(svc.dashboard_data(days))


# --------------------------------------------------------------------------- #
# Incidencias
# --------------------------------------------------------------------------- #
@bp.get("/incidents")
@auth_required
def incidents_list():
    try:
        page = int(request.args.get("page", 1))
    except ValueError:
        page = 1
    result = svc.list_incidents(request.args, page)
    result["rows"] = [incident_json(r) for r in result["rows"]]
    for r in result["rows"]:
        r.pop("source_data", None)  # no hace falta en el listado
    return jsonify(result)


@bp.get("/incidents/export.csv")
@auth_required
def incidents_export():
    return Response(
        svc.export_csv_text(request.args),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=incidencias_{date.today().isoformat()}.csv"},
    )


@bp.post("/incidents")
@auth_required
def incidents_create():
    data, errors = svc.parse_incident_form(body())
    if errors:
        return error(errors[0], 422, errors=errors)
    dup = svc.find_duplicate(data["platform"], data["order_ref"], data["restaurant_id"])
    iid = svc.create_incident(data)
    return jsonify({"id": iid, "duplicate_of": dup}), 201


@bp.get("/incidents/<int:iid>")
@auth_required
def incidents_get(iid):
    inc, err = load_incident(iid)
    if err:
        return err
    return jsonify({
        "incident": incident_json(inc),
        "events": [dict(e) for e in svc.get_events(iid)],
        "claim_text": svc.build_claim_text(inc),
        "claim_emails": claims.history(iid) if svc.is_admin() else [],
        "claimable": inc["platform"] in claims.CLAIM_PLATFORMS,
    })


@bp.put("/incidents/<int:iid>")
@auth_required
def incidents_update(iid):
    inc, err = load_incident(iid)
    if err:
        return err
    data, errors = svc.parse_incident_form(body())
    if not errors:
        errors = svc.update_incident(inc, data)
    if errors:
        return error(errors[0], 422, errors=errors)
    return jsonify({"ok": True})


@bp.post("/incidents/<int:iid>/status")
@auth_required
def incidents_status(iid):
    inc, err = load_incident(iid)
    if err:
        return err
    ok, msg, level = svc.change_status(inc, body())
    if not ok:
        return error(msg, 409 if level == "warning" else 422)
    return jsonify({"ok": True, "message": msg})


@bp.post("/incidents/<int:iid>/comments")
@auth_required
def incidents_comment(iid):
    inc, err = load_incident(iid)
    if err:
        return err
    if not svc.add_comment(inc, str(body().get("comment", ""))):
        return error("El comentario está vacío.", 422)
    return jsonify({"ok": True}), 201


@bp.delete("/incidents/<int:iid>")
@auth_required
@admin_required
def incidents_delete(iid):
    _inc, err = load_incident(iid)
    if err:
        return err
    svc.delete_incident(iid)
    return jsonify({"ok": True})


# --------------------------------------------------------------------------- #
# Importación de Excel / CSV de los portales de delivery
# --------------------------------------------------------------------------- #
# Flujo:  POST /import/preview (subir archivo, o recalcular con otro día)  ->  POST /import/confirm
# Solo el administrador importa: cada Excel trae los pedidos de TODOS los restaurantes.
# El local de cada fila sale del propio Excel; si no existe todavía, se crea al importar.

_FILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,64}$")


class ImportProblem(Exception):
    """Error comprensible para el usuario durante la importación."""


def _upload_path(file_id, ext):
    # El id va ligado al usuario: nadie puede confirmar un archivo subido por otro.
    return os.path.join(current_app.config["UPLOAD_DIR"], f"{g.user['id']}_{file_id}{ext}")


def find_upload(file_id):
    if _FILE_ID_RE.match(str(file_id or "")):
        for ext in ALLOWED_EXT:
            if os.path.exists(p := _upload_path(file_id, ext)):
                return p
    raise ImportProblem("El archivo ha caducado o no existe. Vuelve a subirlo.")


def save_upload(f):
    """Guarda el archivo subido. Devuelve (file_id, ruta, nombre original)."""
    limit = time.time() - UPLOAD_TTL
    for p in glob.glob(os.path.join(current_app.config["UPLOAD_DIR"], "*")):
        try:
            if os.path.getmtime(p) < limit:
                os.remove(p)
        except OSError:
            pass
    if f is None or not f.filename:
        raise ImportProblem("Adjunta el archivo Excel (.xlsx).")
    filename = secure_filename(f.filename) or "archivo.xlsx"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXT:
        raise ImportProblem("El archivo tiene que ser un Excel (.xlsx).")
    file_id = secrets.token_urlsafe(24)
    path = _upload_path(file_id, ext)
    f.save(path)
    return file_id, path, filename


def parse_ref_date(value):
    try:
        return date.fromisoformat(str(value or ""))
    except ValueError:
        return None


def _match_restaurant(name, code, restaurants):
    """Reconoce un local del Excel entre los existentes por código o por nombre («PLENI» -> «Plenilunio»)."""
    n = importer.normalize(name)
    for r in restaurants:
        short = importer.normalize(r["name"]).replace("mcdonald s", "").strip()
        if r["code"].upper() == code or short == n or (len(n) >= 4 and short.startswith(n)):
            return r
    return None


def analyse_upload(path, filename, ref_date=None):
    """Lee el archivo y calcula qué pasaría al importarlo. No escribe nada en la BD."""
    try:
        table = importer.read_table(path)
    except ValueError as exc:
        raise ImportProblem(str(exc))
    except Exception as exc:  # archivo corrupto, protegido con contraseña, etc.
        current_app.logger.warning("Error leyendo importación: %s", exc)
        raise ImportProblem("No se pudo leer el archivo. ¿Es un Excel (.xlsx) válido?")

    profile = importer.PROFILES[table["profile"]]
    platform = profile["platform"]
    notices = []
    if profile.get("needs_date") and not ref_date:
        guessed = importer.month_from_filename(filename)
        ref_date = min(guessed, date.today()) if guessed else date.today()
        notices.append(f"El archivo no indica el día de los pedidos: se usa el {ref_date:%d/%m/%Y}"
                       + (" (mes del nombre del archivo)" if guessed else " (hoy)") + ". Puedes cambiarlo.")
    items = importer.analyse(table, {"filename": filename, "ref_date": ref_date})

    # Locales del Excel: recordado de antes, reconocido por nombre o nuevo
    conn = db.get_db()
    restaurants = svc.get_restaurants()
    stores = OrderedDict()
    for it in items:
        if it["store"]:
            stores.setdefault(it["store"], {"name": it["store"], "rows": 0})["rows"] += 1
    for key, st in stores.items():
        alias = conn.execute("SELECT r.* FROM restaurant_aliases a JOIN restaurants r ON r.id = a.restaurant_id "
                             "WHERE a.platform = ? AND a.ext_key = ?", (platform, key)).fetchone()
        sug = importer.suggest_restaurant(key)
        match = alias or _match_restaurant(key, sug["code"], restaurants)
        st.update(restaurant_id=match["id"] if match else None, suggestion=sug, is_new=match is None,
                  restaurant_name=match["name"] if match else sug["name"])

    # Estado de cada fila: nueva / ya importada antes / repetida en el archivo / con error
    seen = set()
    for it in items:
        if it["error"]:
            it["status"] = "error"
            continue
        ref = it["data"]["order_ref"]
        if ref in seen:
            it["status"] = "repetida"
        elif conn.execute("SELECT 1 FROM incidents WHERE platform = ? AND order_ref = ?", (platform, ref)).fetchone():
            it["status"] = "duplicada"
        else:
            it["status"] = "nueva"
        seen.add(ref)

    new = [it for it in items if it["status"] == "nueva"]
    count = lambda s: sum(it["status"] == s for it in items)
    summary = {
        "rows": len(items), "new": len(new), "duplicates": count("duplicada"), "repeated": count("repetida"),
        "errors": count("error"), "claimed_total": round(sum(it["data"]["claimed_amount"] for it in new), 2),
        "new_stores": sum(st["is_new"] for st in stores.values()),
    }
    return {"profile": table["profile"], "platform": platform, "items": items, "stores": stores,
            "summary": summary, "notices": notices, "ref_date": ref_date}


_STATUS_ORDER = {"nueva": 0, "error": 1, "repetida": 2, "duplicada": 3}


def public_preview(ctx, file_id, filename):
    """Respuesta JSON de la vista previa (la usan el frontend y la web clásica)."""
    p = importer.PROFILES[ctx["profile"]]
    items = sorted(ctx["items"], key=lambda it: (_STATUS_ORDER[it["status"]], it["row"]))[:MAX_PREVIEW_ITEMS]
    return {
        "file_id": file_id, "filename": filename, "platform": ctx["platform"],
        "profile": {"label": p["label"], "explain": p["explain"], "needs_date": bool(p.get("needs_date"))},
        "ref_date": ctx["ref_date"].isoformat() if ctx["ref_date"] else None,
        "notices": ctx["notices"],
        "summary": ctx["summary"],
        "stores": [{k: st[k] for k in ("name", "rows", "restaurant_name", "is_new")} for st in ctx["stores"].values()],
        "items": [
            {"row": it["row"], "status": it["status"], "error": it["error"], "warnings": it["warnings"],
             "headline": it["headline"], "approx_date": it["approx_date"], "store": it["store"],
             "restaurant_name": ctx["stores"][it["store"]]["restaurant_name"] if it["store"] in ctx["stores"] else None,
             **({k: it["data"][k] for k in ("order_ref", "order_date", "claimed_amount")} if it["data"] else {})}
            for it in items
        ],
        "items_truncated": len(ctx["items"]) > MAX_PREVIEW_ITEMS,
    }


def run_import(path, filename, ref_date=None):
    """Analiza de nuevo el archivo y crea locales e incidencias. Lo usan la API y la web clásica."""
    ctx = analyse_upload(path, filename, ref_date)
    s, platform = ctx["summary"], ctx["platform"]
    conn = db.get_db()
    created_restaurants = []
    for key, st in ctx["stores"].items():
        if st["is_new"]:
            code, n = st["suggestion"]["code"], 2
            while conn.execute("SELECT 1 FROM restaurants WHERE code = ?", (code,)).fetchone():
                code, n = f"{st['suggestion']['code']}-{n}", n + 1
            st["restaurant_id"] = conn.execute("INSERT INTO restaurants (code, name) VALUES (?, ?)",
                                               (code, st["suggestion"]["name"][:120])).lastrowid
            created_restaurants.append({"id": st["restaurant_id"], "code": code, "name": st["suggestion"]["name"]})
        conn.execute("""INSERT INTO restaurant_aliases (platform, ext_key, ext_name, restaurant_id) VALUES (?, ?, ?, ?)
                        ON CONFLICT(platform, ext_key) DO UPDATE SET restaurant_id = excluded.restaurant_id""",
                     (platform, key, st["name"], st["restaurant_id"]))

    batch_id = conn.execute(
        """INSERT INTO import_batches (platform, restaurant_id, filename, total_rows, profile, user_id, created_at)
           VALUES (?, NULL, ?, ?, ?, ?, ?)""",
        (platform, filename, s["rows"], ctx["profile"], g.user["id"], svc.now_str()),
    ).lastrowid
    created = 0
    for it in ctx["items"]:
        if it["status"] == "nueva":
            incident = {**it["data"], "platform": platform, "restaurant_id": ctx["stores"][it["store"]]["restaurant_id"]}
            source = [["Archivo", filename], ["Fila del archivo", str(it["row"])]] + it["source"]
            svc.create_incident(incident, f"Importada desde «{filename}» (lote #{batch_id}, fila {it['row']}).",
                                batch_id=batch_id, commit=False, source_data=source)
            created += 1
    conn.execute("UPDATE import_batches SET created_count = ?, duplicate_count = ?, error_count = ? WHERE id = ?",
                 (created, s["duplicates"] + s["repeated"], s["errors"], batch_id))
    conn.commit()
    try:
        os.remove(path)
    except OSError:
        pass
    return {"batch_id": batch_id, "created": created, "duplicates": s["duplicates"], "repeated": s["repeated"],
            "errors": s["errors"], "claimed_total": s["claimed_total"], "created_restaurants": created_restaurants}


def list_batches():
    rows = db.get_db().execute(
        """SELECT b.*, u.full_name AS user_name FROM import_batches b LEFT JOIN users u ON u.id = b.user_id
           ORDER BY b.id DESC LIMIT 30""").fetchall()
    return [dict(r) for r in rows]


@bp.post("/import/preview")
@auth_required
@admin_required
def import_preview():
    """Sube el archivo (o reutiliza el ya subido, p. ej. al cambiar el día) y devuelve la vista previa."""
    try:
        if file_id := request.form.get("file_id"):
            path, filename = find_upload(file_id), secure_filename(request.form.get("filename", "")) or "archivo.xlsx"
        else:
            file_id, path, filename = save_upload(request.files.get("file"))
        ctx = analyse_upload(path, filename, parse_ref_date(request.form.get("ref_date")))
    except ImportProblem as exc:
        return error(str(exc), 422)
    return jsonify(public_preview(ctx, file_id, filename))


@bp.post("/import/confirm")
@auth_required
@admin_required
def import_confirm():
    data = body()
    try:
        path = find_upload(data.get("file_id"))
        filename = secure_filename(str(data.get("filename", ""))) or "archivo.xlsx"
        result = run_import(path, filename, parse_ref_date(data.get("ref_date")))
    except ImportProblem as exc:
        return error(str(exc), 422)
    return jsonify(result), 201


@bp.get("/import/batches")
@auth_required
@admin_required
def import_batches():
    return jsonify(list_batches())


# --------------------------------------------------------------------------- #
# Reclamaciones por correo (ver claims.py). Solo administrador.
# --------------------------------------------------------------------------- #
def _ids(data):
    """IDs elegidos a mano (incident_ids) o todas las incidencias que cumplen los filtros del listado."""
    if isinstance(data.get("filters"), dict):
        where, params = svc.build_filters({k: str(v) for k, v in data["filters"].items()})
        marks = ",".join("?" * len(claims.CLAIM_PLATFORMS))
        rows = db.get_db().execute(
            f"SELECT i.id FROM incidents i WHERE {where} AND i.platform IN ({marks}) ORDER BY i.order_date LIMIT ?",
            [*params, *claims.CLAIM_PLATFORMS, claims.MAX_PER_REQUEST]).fetchall()
        return [r["id"] for r in rows]
    raw = data.get("incident_ids")
    return [int(i) for i in raw if str(i).isdigit()] if isinstance(raw, list) else []


@bp.get("/mail/settings")
@auth_required
@admin_required
def mail_settings_get():
    return jsonify(claims.public_settings())


@bp.put("/mail/settings")
@auth_required
@admin_required
def mail_settings_put():
    try:
        claims.save_settings(body())
    except claims.MailProblem as exc:
        return error(str(exc), 422)
    return jsonify(claims.public_settings())


@bp.post("/mail/test")
@auth_required
@admin_required
def mail_test():
    try:
        to = claims.send_test(str(body().get("to", "")))
    except claims.MailProblem as exc:
        return error(str(exc), 422)
    return jsonify({"ok": True, "message": f"Correo de prueba enviado a {to}. Revisa la bandeja (y la carpeta de spam)."})


@bp.post("/claims/preview")
@auth_required
@admin_required
def claims_preview():
    return jsonify(claims.preview(_ids(body())))


@bp.post("/claims/send")
@auth_required
@admin_required
def claims_send():
    try:
        return jsonify(claims.send_claims(_ids(body())))
    except claims.MailProblem as exc:
        return error(str(exc), 422)


@bp.errorhandler(413)
def too_large(_e):
    return error("El archivo supera el máximo de 10 MB.", 413)
