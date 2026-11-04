"""Reclamaciones por correo electrónico: un correo independiente por cada pedido con incidencia.

Fase 1 (actual): solo Uber Eats y Just Eat, y en MODO PRUEBA por defecto. En modo prueba
todos los correos van a la dirección de prueba (no a la plataforma) y el asunto empieza por
[PRUEBA]; la incidencia no cambia de estado.

La configuración SMTP se guarda en instance/correo.json (carpeta excluida de git). La contraseña
se guarda en ese archivo en claro: protege el acceso al equipo/servidor.

ANTI-SPAM: los correos no se envían todos de golpe. Se ponen en una cola (claim_emails con
status 'pendiente') y un hilo en segundo plano los envía de uno en uno, con una pausa variable
entre correos y con límites por hora y por día. Si Gmail avisa de que vamos demasiado rápido
(errores 421/450/«rate»/«quota»), la cola se pausa sola y continúa después, sin perder correos.
"""
import json
import os
import random
import re
import smtplib
import ssl
import threading
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from flask import current_app, g

import db
import services as svc
from constants import DT_FMT, PLATFORMS

CLAIM_PLATFORMS = ("ubereats", "justeat")  # Glovo queda fuera de esta fase
MAX_PER_REQUEST = 500
MAX_ATTEMPTS = 3  # reintentos por correo ante fallos de conexión
LIMITS = {  # clave: (mínimo, máximo, nombre para los mensajes)
    "interval_seconds": (2, 600, "tiempo entre correos"),
    "hourly_limit": (1, 2000, "límite por hora"),
    "daily_limit": (1, 2000, "límite por día"),
}

PRESETS = {  # servidores habituales (el usuario puede escribir otro)
    "gmail": {"host": "smtp.gmail.com", "port": 587, "security": "starttls"},
    "outlook": {"host": "smtp.office365.com", "port": 587, "security": "starttls"},
}

DEFAULTS = {
    "host": "", "port": 587, "security": "starttls", "username": "", "password": "",
    "sender": "", "sender_name": "Reclamaciones McDonald's",
    "test_mode": True, "test_recipient": "",
    "recipients": {"ubereats": "", "justeat": ""},
    "signature": "Departamento de delivery\nMcDonald's",
    # Envío gradual. Gmail personal permite ~500 correos/día; nos quedamos muy por debajo.
    "interval_seconds": 20, "hourly_limit": 60, "daily_limit": 300,
}

_EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


class MailProblem(Exception):
    """Error comprensible para el usuario."""


# --------------------------------------------------------------------------- #
# Configuración
# --------------------------------------------------------------------------- #
def _path():
    return os.path.join(current_app.instance_path, "correo.json")


def load_settings():
    s = json.loads(json.dumps(DEFAULTS))
    try:
        with open(_path(), encoding="utf-8") as fh:
            stored = json.load(fh)
        s.update({k: v for k, v in stored.items() if k in DEFAULTS})
        s["recipients"] = {**DEFAULTS["recipients"], **(stored.get("recipients") or {})}
    except (OSError, ValueError):
        pass
    return s


def public_settings():
    """Configuración sin la contraseña (para enviarla al navegador)."""
    s = load_settings()
    out = {k: v for k, v in s.items() if k != "password"}
    out.update(password_set=bool(s["password"]), presets=PRESETS, missing=missing_settings(s),
               platforms=list(CLAIM_PLATFORMS))
    return out


def missing_settings(s):
    missing = [label for key, label in (("host", "servidor SMTP"), ("sender", "remitente"),
                                        ("username", "usuario"), ("password", "contraseña")) if not s.get(key)]
    if s.get("test_mode") and not s.get("test_recipient"):
        missing.append("correo de prueba")
    return missing


def save_settings(data):
    """Valida y guarda. Si la contraseña viene vacía se conserva la anterior."""
    s = load_settings()
    text = lambda k, n=200: str(data.get(k, s[k]) or "").strip()[:n]
    for key in ("host", "username", "sender", "sender_name", "test_recipient"):
        s[key] = text(key)
    s["signature"] = str(data.get("signature", s["signature"]) or "")[:1000]
    if data.get("password"):
        s["password"] = str(data["password"]).strip()[:500]
        if "gmail" in s.get("host", "") or "gmail" in str(data.get("host", "")):
            s["password"] = s["password"].replace(" ", "")  # Google la muestra en grupos: "abcd efgh ijkl mnop"
    try:
        s["port"] = int(data.get("port", s["port"]))
    except (TypeError, ValueError):
        raise MailProblem("El puerto tiene que ser un número (normalmente 587).")
    if not 1 <= s["port"] <= 65535:
        raise MailProblem("Puerto no válido.")
    s["security"] = data.get("security", s["security"])
    if s["security"] not in ("starttls", "ssl", "none"):
        raise MailProblem("Seguridad no válida.")
    for key, (lo, hi, label) in LIMITS.items():
        try:
            s[key] = int(data.get(key, s[key]))
        except (TypeError, ValueError):
            raise MailProblem(f"El {label} tiene que ser un número.")
        if not lo <= s[key] <= hi:
            raise MailProblem(f"El {label} debe estar entre {lo} y {hi}.")
    s["test_mode"] = bool(data.get("test_mode", s["test_mode"]))
    rec = data.get("recipients") or {}
    s["recipients"] = {p: str(rec.get(p, s["recipients"].get(p, "")) or "").strip()[:200] for p in CLAIM_PLATFORMS}

    for label, value in (("remitente", s["sender"]), ("correo de prueba", s["test_recipient"]),
                         *((f"destinatario de {PLATFORMS[p]['name']}", v) for p, v in s["recipients"].items())):
        if value and not _EMAIL_RE.match(value):
            raise MailProblem(f"El {label} no es una dirección de correo válida.")
    if not s["test_mode"] and not all(s["recipients"].values()):
        raise MailProblem("Para salir del modo prueba indica el correo de reclamaciones de cada plataforma.")

    os.makedirs(current_app.instance_path, exist_ok=True)
    with open(_path(), "w", encoding="utf-8") as fh:
        json.dump(s, fh, ensure_ascii=False, indent=2)
    return s


# --------------------------------------------------------------------------- #
# Contenido del correo
# --------------------------------------------------------------------------- #
def _reason(inc):
    """Motivo según el Excel: es la primera línea de la descripción ("Just Eat · Reembolso (REFUND): 26,13 €.")."""
    first = (inc["description"] or "").split("\n", 1)[0]
    m = re.match(r"^[^·]+·\s*([^:]+)", first)
    return m.group(1).strip() if m else "Pedido incorrecto"


def build_email(inc, s):
    pname = PLATFORMS[inc["platform"]]["name"]
    approx = inc["platform"] == "justeat"  # el Excel de Just Eat no trae el día
    try:
        source = json.loads(inc["source_data"] or "[]")
    except ValueError:
        source = []
    subject = f"Reclamación pedido {inc['order_ref']} · {inc['restaurant_name']} · {svc.fmt_money(inc['claimed_amount'])}"
    lines = [
        f"Hola equipo de soporte de {pname}:",
        "",
        f"Desde {inc['restaurant_name']} solicitamos la revisión y el abono del siguiente pedido:",
        "",
        f"  · Plataforma: {pname}",
        f"  · Nº de pedido: {inc['order_ref']}",
        f"  · Fecha: {svc.fmt_dt(inc['order_date'], '%d/%m/%Y') if approx else svc.fmt_dt(inc['order_date'])}"
        + (" (hora " + svc.fmt_dt(inc["order_date"], "%H:%M") + ")" if approx else ""),
        f"  · Restaurante: {inc['restaurant_name']}",
        f"  · Motivo: {_reason(inc)}",
        f"  · Importe descontado que reclamamos: {svc.fmt_money(inc['claimed_amount'])}",
    ]
    if source:
        lines += ["", "Datos del pedido según nuestro registro:"]
        lines += [f"  {'Local' if k.startswith('Columna ') else k}: {v}"
                  for k, v in source if k not in ("Archivo", "Fila del archivo")]
    lines += ["", "Consideramos que el pedido se preparó y entregó correctamente, por lo que solicitamos "
                  "que se revise el cargo y se nos abone el importe indicado.",
              "", "Quedamos a la espera de su respuesta. Muchas gracias.", "", s["signature"]]
    recipient = s["test_recipient"] if s["test_mode"] else s["recipients"].get(inc["platform"], "")
    if s["test_mode"]:
        subject = f"[PRUEBA] {subject}"
        lines = [f"Correo de prueba. En modo real se enviaría a: "
                 f"{s['recipients'].get(inc['platform']) or '(sin configurar)'}", ""] + lines
    return {"to": recipient, "subject": subject, "body": "\n".join(lines)}


def _message(s, to, subject, body, incident_id=None):
    msg = EmailMessage()
    msg["From"] = formataddr((s["sender_name"], s["sender"]))
    msg["To"] = to
    msg["Subject"] = subject
    # Cabeceras que esperan los filtros anti-spam (sin Date ni Message-ID un correo puntúa como sospechoso)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=s["sender"].split("@")[-1])
    msg["Reply-To"] = s["sender"]
    msg["Content-Language"] = "es"
    if incident_id:
        msg["X-Incidencia-ID"] = str(incident_id)
    msg.set_content(body)
    return msg


# --------------------------------------------------------------------------- #
# Envío
# --------------------------------------------------------------------------- #
def _connect(s):
    missing = missing_settings(s)
    if missing:
        raise MailProblem("Falta configurar: " + ", ".join(missing) + ".")
    try:
        if s["security"] == "ssl":
            server = smtplib.SMTP_SSL(s["host"], s["port"], timeout=20, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(s["host"], s["port"], timeout=20)
            if s["security"] == "starttls":
                server.starttls(context=ssl.create_default_context())
    except (OSError, smtplib.SMTPException) as exc:
        raise MailProblem(f"No se pudo conectar con {s['host']}:{s['port']} ({exc}). "
                          "Revisa el servidor, el puerto y que el antivirus o la red no bloqueen el correo.")
    # Gmail rechaza la contraseña normal: a veces responde 534/535 y a veces corta la conexión sin más
    hint = (" En Gmail hace falta una «contraseña de aplicación» de 16 letras (no la contraseña normal), "
            "con la verificación en dos pasos activada: myaccount.google.com/apppasswords"
            if "gmail" in s["host"] else "")
    try:
        server.login(s["username"], s["password"])
        return server
    except smtplib.SMTPAuthenticationError as exc:
        raise MailProblem(f"El servidor rechazó el usuario o la contraseña ({exc.smtp_code}).{hint}")
    except (OSError, smtplib.SMTPException):
        raise MailProblem(f"El servidor cortó la conexión al iniciar sesión: usuario o contraseña no aceptados.{hint}")


def send_test(to=None):
    s = load_settings()
    to = (to or s["test_recipient"]).strip()
    if not _EMAIL_RE.match(to):
        raise MailProblem("Indica un correo de prueba válido.")
    server = _connect(s)
    try:
        server.send_message(_message(s, to, "[PRUEBA] Configuración de correo de Delivery Hub",
                                     "Si lees esto, la configuración de correo funciona.\n\n" + s["signature"]))
    finally:
        server.quit()
    return to


def _claimable(iid):
    inc = svc.get_incident(iid)
    if inc is None:
        return None, "Incidencia no encontrada."
    if inc["platform"] not in CLAIM_PLATFORMS:
        return inc, f"{PLATFORMS[inc['platform']]['name']} no está incluida en las reclamaciones por correo."
    return inc, None


def already_sent_real(iid):
    return db.get_db().execute(
        "SELECT 1 FROM claim_emails WHERE incident_id = ? AND mode = 'real' AND status = 'enviado'", (iid,)).fetchone()


def _in_queue(iid):
    return db.get_db().execute(
        "SELECT 1 FROM claim_emails WHERE incident_id = ? AND status = 'pendiente'", (iid,)).fetchone()


def preview(ids):
    s = load_settings()
    out = []
    for iid in ids[:MAX_PER_REQUEST]:
        inc, err = _claimable(iid)
        if err:
            out.append({"incident_id": iid, "error": err})
        else:
            out.append({"incident_id": iid, "order_ref": inc["order_ref"], "platform": inc["platform"],
                        "already_sent": bool(already_sent_real(iid)), "in_queue": bool(_in_queue(iid)),
                        **build_email(inc, s)})
    return {"test_mode": s["test_mode"], "emails": out, "queue": queue_status()}


# --------------------------------------------------------------------------- #
# Cola de envío gradual
# --------------------------------------------------------------------------- #
def enqueue_claims(ids):
    """Pone en cola un correo por incidencia. El envío real lo hace el hilo de fondo."""
    s = load_settings()
    mode = "prueba" if s["test_mode"] else "real"
    ids = list(dict.fromkeys(int(i) for i in ids))[:MAX_PER_REQUEST]
    if not ids:
        raise MailProblem("No has seleccionado ninguna incidencia.")
    _quit(_connect(s))  # comprueba la cuenta ahora, para no encolar con una contraseña mala

    conn, ts = db.get_db(), svc.now_str()
    queued, skipped = 0, []
    for iid in ids:
        inc, err = _claimable(iid)
        if not err and _in_queue(iid):
            err = "Ya está en la cola de envío."
        if not err and mode == "real" and already_sent_real(iid):
            err = "Ya se envió la reclamación real de este pedido."
        if err:
            skipped.append({"incident_id": iid, "error": err})
            continue
        mail = build_email(inc, s)
        conn.execute(
            """INSERT INTO claim_emails (incident_id, mode, recipient, subject, body, status, sent_by, queued_at)
               VALUES (?, ?, ?, ?, ?, 'pendiente', ?, ?)""",
            (iid, mode, mail["to"], mail["subject"], mail["body"], g.user["id"], ts))
        queued += 1
    conn.commit()
    if queued:
        start_worker()
    return {"mode": mode, "queued": queued, "skipped": len(skipped), "skipped_details": skipped[:20],
            "queue": queue_status()}


# Estado del hilo de envío (compartido entre peticiones)
STATE = {"paused_until": None, "last_error": None, "next_at": None}
_lock = threading.Lock()
_wake = threading.Event()
_worker = None


def start_worker(app=None):
    """Arranca el hilo de envío si hay correos pendientes y no está ya en marcha."""
    global _worker
    app = app or current_app._get_current_object()
    with _lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run, args=(app,), daemon=True, name="envio-correos")
            _worker.start()


def worker_running():
    return _worker is not None and _worker.is_alive()


def _quit(server):
    if server is not None:
        try:
            server.quit()
        except (smtplib.SMTPException, OSError):
            pass


def _count_sent(conn, since):
    return conn.execute("SELECT COUNT(*) FROM claim_emails WHERE status = 'enviado' AND sent_at >= ?",
                        (since.strftime(DT_FMT),)).fetchone()[0]


def _blocked_until(conn, s):
    """Momento hasta el que hay que esperar por pausa o por límites; None si se puede enviar ya."""
    now = datetime.now()
    if STATE["paused_until"] and now < STATE["paused_until"]:
        return STATE["paused_until"]
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if _count_sent(conn, midnight) >= s["daily_limit"]:
        STATE["last_error"] = f"Límite diario de {s['daily_limit']} correos alcanzado: los pendientes salen mañana."
        return midnight + timedelta(days=1, minutes=1)
    hour_ago = now - timedelta(hours=1)
    if _count_sent(conn, hour_ago) >= s["hourly_limit"]:
        first = conn.execute("SELECT MIN(sent_at) FROM claim_emails WHERE status = 'enviado' AND sent_at >= ?",
                             (hour_ago.strftime(DT_FMT),)).fetchone()[0]
        return datetime.strptime(first, DT_FMT) + timedelta(hours=1, seconds=5)
    return None


def _event(conn, iid, uid, kind, msg):
    conn.execute("INSERT INTO incident_events (incident_id, user_id, event_type, message, created_at) "
                 "VALUES (?, ?, ?, ?, ?)", (iid, uid, kind, msg, svc.now_str()))


def _send_one(conn, s, item, server):
    """Envía un correo de la cola. Devuelve (conexión SMTP, segundos hasta el siguiente)."""
    interval = s["interval_seconds"] * random.uniform(0.75, 1.5)  # ritmo irregular, como una persona
    try:
        if server is None:
            server = _connect(s)
        server.send_message(_message(s, item["recipient"], item["subject"], item["body"], item["incident_id"]))
    except MailProblem as exc:  # no conecta o no acepta la contraseña: se pausa toda la cola
        STATE["last_error"] = str(exc)
        STATE["paused_until"] = datetime.now() + timedelta(minutes=15)
        return None, 0
    except smtplib.SMTPRecipientsRefused as exc:  # dirección rechazada: falla solo este correo
        _fail(conn, item, f"Destinatario rechazado: {list(exc.recipients.values())[0]}")
        return server, interval
    except smtplib.SMTPResponseException as exc:
        text = (exc.smtp_error.decode("utf-8", "replace") if isinstance(exc.smtp_error, bytes) else str(exc.smtp_error))
        low = text.lower()
        if "daily" in low or "quota" in low or "5.4.5" in low:  # cupo diario de Gmail agotado
            STATE["last_error"] = f"Gmail ha alcanzado su límite diario de envíos ({exc.smtp_code}). Se reanuda mañana."
            STATE["paused_until"] = datetime.now().replace(hour=0, minute=0, second=0) + timedelta(days=1, minutes=5)
            _quit(server)
            return None, 0
        if 400 <= exc.smtp_code < 500 or "rate" in low or "try again" in low:  # vamos demasiado rápido
            STATE["last_error"] = f"El servidor pide ir más despacio ({exc.smtp_code}). Cola en pausa 30 minutos."
            STATE["paused_until"] = datetime.now() + timedelta(minutes=30)
            _quit(server)
            return None, 0
        _fail(conn, item, f"{exc.smtp_code} {text[:300]}")  # rechazo definitivo de este correo
        return server, interval
    except (smtplib.SMTPException, OSError) as exc:  # se cortó la conexión: reintentar este correo
        _quit(server)
        attempts = item["attempts"] + 1
        if attempts >= MAX_ATTEMPTS:
            _fail(conn, item, f"No se pudo enviar tras {attempts} intentos: {exc}")
        else:
            conn.execute("UPDATE claim_emails SET attempts = ?, error = ? WHERE id = ?", (attempts, str(exc), item["id"]))
            conn.commit()
        return None, 60

    ts = svc.now_str()
    iid, uid = item["incident_id"], item["sent_by"]
    conn.execute("UPDATE claim_emails SET status = 'enviado', sent_at = ?, error = NULL, attempts = attempts + 1 "
                 "WHERE id = ?", (ts, item["id"]))
    _event(conn, iid, uid, "correo", f"Correo de reclamación {'de PRUEBA ' if item['mode'] == 'prueba' else ''}"
                                     f"enviado a {item['recipient']}.")
    if item["mode"] == "real":
        cur = conn.execute("UPDATE incidents SET status = 'reclamada', claimed_at = ?, updated_at = ? "
                           "WHERE id = ? AND status = 'abierta'", (ts, ts, iid))
        if cur.rowcount:
            _event(conn, iid, uid, "estado", "Estado: Abierta → Reclamada a plataforma (por correo).")
    conn.commit()
    STATE["last_error"] = None
    return server, interval


def _fail(conn, item, error):
    conn.execute("UPDATE claim_emails SET status = 'error', error = ?, attempts = attempts + 1, sent_at = ? WHERE id = ?",
                 (error, svc.now_str(), item["id"]))
    _event(conn, item["incident_id"], item["sent_by"], "correo", f"Error al enviar el correo de reclamación: {error}")
    conn.commit()


def _run(app):
    """Bucle del hilo de envío: un correo cada vez, respetando pausas y límites. Termina cuando no queda nada."""
    server = None
    try:
        while True:
            with app.app_context():
                conn, s = db.get_db(), load_settings()
                item = conn.execute(
                    "SELECT * FROM claim_emails WHERE status = 'pendiente' ORDER BY id LIMIT 1").fetchone()
                if item is None:
                    STATE["next_at"] = None
                    return
                wait_until = _blocked_until(conn, s)
                if wait_until is None:
                    server, delay = _send_one(conn, s, item, server)
                    wait_until = datetime.now() + timedelta(seconds=delay)
                STATE["next_at"] = wait_until
            seconds = (wait_until - datetime.now()).total_seconds()
            if seconds > 60:  # no mantener abierta la conexión SMTP durante esperas largas
                _quit(server)
                server = None
            if seconds > 0:
                _wake.wait(timeout=seconds)
                _wake.clear()
    except Exception as exc:  # nunca dejar el hilo muerto sin explicación
        app.logger.exception("Error en el envío de correos")
        STATE["last_error"] = f"Error inesperado en el envío: {exc}"
    finally:
        _quit(server)


def queue_status():
    conn, s, now = db.get_db(), load_settings(), datetime.now()
    pending = conn.execute("SELECT COUNT(*) FROM claim_emails WHERE status = 'pendiente'").fetchone()[0]
    if pending and not worker_running():  # p. ej. tras reiniciar el servidor
        start_worker()
    per_hour = min(s["hourly_limit"], 3600 / (s["interval_seconds"] * 1.125))
    recent = conn.execute(
        """SELECT e.id, e.incident_id, i.order_ref, i.platform, e.mode, e.recipient, e.status, e.error,
                  e.queued_at, e.sent_at
           FROM claim_emails e JOIN incidents i ON i.id = e.incident_id WHERE e.hidden = 0
           ORDER BY e.id DESC LIMIT 15""").fetchall()
    iso = lambda d: d.strftime(DT_FMT) if d else None
    paused = STATE["paused_until"] if STATE["paused_until"] and STATE["paused_until"] > now else None
    return {
        "pending": pending,
        "sent_today": _count_sent(conn, now.replace(hour=0, minute=0, second=0, microsecond=0)),
        "sent_last_hour": _count_sent(conn, now - timedelta(hours=1)),
        "interval_seconds": s["interval_seconds"], "hourly_limit": s["hourly_limit"], "daily_limit": s["daily_limit"],
        "eta_minutes": round(pending / per_hour * 60) if pending else 0,
        "running": worker_running(), "paused_until": iso(paused), "next_at": iso(STATE["next_at"]) if pending else None,
        "last_error": STATE["last_error"] if pending else None,
        "recent": [dict(r) for r in recent],
    }


def cancel_pending():
    conn = db.get_db()
    n = conn.execute("UPDATE claim_emails SET status = 'cancelado', error = 'Cancelado por el usuario', sent_at = ? "
                     "WHERE status = 'pendiente'", (svc.now_str(),)).rowcount
    conn.commit()
    _wake.set()
    return n


def clear_history():
    """Vacía la cola de envío que se ve en pantalla: cancela lo pendiente y oculta todo el historial.

    Los registros no se borran de verdad para que sigan contando en los límites por hora/día
    (si no, borrar permitiría saltarse el cupo de Gmail) y para que una reclamación REAL ya
    enviada no se pueda volver a mandar por error.
    """
    conn = db.get_db()
    cancelled = conn.execute("UPDATE claim_emails SET status = 'cancelado', error = 'Cancelado por el usuario', "
                             "sent_at = ? WHERE status = 'pendiente'", (svc.now_str(),)).rowcount
    hidden = conn.execute("UPDATE claim_emails SET hidden = 1 WHERE hidden = 0").rowcount
    conn.commit()
    STATE.update(paused_until=None, last_error=None)
    _wake.set()
    return {"cancelled": cancelled, "cleared": hidden}


def resume():
    """Quita la pausa (p. ej. tras corregir la contraseña) y vuelve a intentarlo ya."""
    STATE["paused_until"] = None
    STATE["last_error"] = None
    start_worker()
    _wake.set()


def history(iid):
    return [dict(r) for r in db.get_db().execute(
        """SELECT e.id, e.mode, e.recipient, e.subject, e.status, e.error, COALESCE(e.sent_at, e.queued_at) AS sent_at,
                  u.full_name AS sent_by_name
           FROM claim_emails e LEFT JOIN users u ON u.id = e.sent_by WHERE e.incident_id = ? AND e.hidden = 0
           ORDER BY e.id DESC""",
        (iid,))]
