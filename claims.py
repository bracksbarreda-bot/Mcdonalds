"""Reclamaciones por correo electrónico: un correo independiente por cada pedido con incidencia.

Fase 1 (actual): solo Uber Eats y Just Eat, y en MODO PRUEBA por defecto. En modo prueba
todos los correos van a la dirección de prueba (no a la plataforma) y el asunto empieza por
[PRUEBA]; la incidencia no cambia de estado.

La configuración SMTP se guarda en instance/correo.json (carpeta excluida de git). La contraseña
se guarda en ese archivo en claro: protege el acceso al equipo/servidor.
"""
import json
import os
import re
import smtplib
import ssl
import time
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from flask import current_app, g

import db
import services as svc
from constants import PLATFORMS

CLAIM_PLATFORMS = ("ubereats", "justeat")  # Glovo queda fuera de esta fase
MAX_PER_REQUEST = 200
SEND_PAUSE = 0.3  # segundos entre correos, para no saturar el servidor SMTP

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
        lines = [f"*** CORREO DE PRUEBA · En modo real iría a: {s['recipients'].get(inc['platform']) or '(sin configurar)'} ***",
                 ""] + lines
    return {"to": recipient, "subject": subject, "body": "\n".join(lines)}


def _message(s, to, subject, body, incident_id=None):
    msg = EmailMessage()
    msg["From"] = formataddr((s["sender_name"], s["sender"]))
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=s["sender"].split("@")[-1])
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


def preview(ids):
    s = load_settings()
    out = []
    for iid in ids[:MAX_PER_REQUEST]:
        inc, err = _claimable(iid)
        if err:
            out.append({"incident_id": iid, "error": err})
        else:
            out.append({"incident_id": iid, "order_ref": inc["order_ref"], "platform": inc["platform"],
                        "already_sent": bool(already_sent_real(iid)), **build_email(inc, s)})
    return {"test_mode": s["test_mode"], "emails": out}


def send_claims(ids):
    """Envía un correo por incidencia. Devuelve el resultado de cada una."""
    s = load_settings()
    mode = "prueba" if s["test_mode"] else "real"
    ids = list(dict.fromkeys(int(i) for i in ids))[:MAX_PER_REQUEST]
    if not ids:
        raise MailProblem("No has seleccionado ninguna incidencia.")
    server = _connect(s)
    conn = db.get_db()
    results = []
    try:
        for n, iid in enumerate(ids):
            inc, err = _claimable(iid)
            if not err and mode == "real" and already_sent_real(iid):
                err = "Ya se envió la reclamación real de este pedido."
            if err:
                results.append({"incident_id": iid, "status": "omitido", "error": err})
                continue
            mail = build_email(inc, s)
            status, error = "enviado", None
            try:
                if n:
                    time.sleep(SEND_PAUSE)
                server.send_message(_message(s, mail["to"], mail["subject"], mail["body"], iid))
            except smtplib.SMTPServerDisconnected:  # reconecta una vez si el servidor cortó
                try:
                    server = _connect(s)
                    server.send_message(_message(s, mail["to"], mail["subject"], mail["body"], iid))
                except (MailProblem, smtplib.SMTPException, OSError) as exc:
                    status, error = "error", str(exc)
            except (smtplib.SMTPException, OSError) as exc:
                status, error = "error", str(exc)
            ts = svc.now_str()
            conn.execute(
                """INSERT INTO claim_emails (incident_id, mode, recipient, subject, body, status, error, sent_by, sent_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (iid, mode, mail["to"], mail["subject"], mail["body"], status, error, g.user["id"], ts))
            if status == "enviado":
                svc.add_event(iid, "correo", f"Correo de reclamación {'de PRUEBA ' if mode == 'prueba' else ''}"
                                             f"enviado a {mail['to']}.")
                if mode == "real" and inc["status"] == "abierta":
                    conn.execute("UPDATE incidents SET status = 'reclamada', claimed_at = ?, updated_at = ? WHERE id = ?",
                                 (ts, ts, iid))
                    svc.add_event(iid, "estado", "Estado: Abierta → Reclamada a plataforma (por correo).")
            else:
                svc.add_event(iid, "correo", f"Error al enviar el correo de reclamación: {error}")
            conn.commit()
            results.append({"incident_id": iid, "order_ref": inc["order_ref"], "status": status, "error": error,
                            "to": mail["to"]})
    finally:
        try:
            server.quit()
        except (smtplib.SMTPException, OSError):
            pass
    return {"mode": mode, "results": results,
            "sent": sum(r["status"] == "enviado" for r in results),
            "failed": sum(r["status"] == "error" for r in results),
            "skipped": sum(r["status"] == "omitido" for r in results)}


def history(iid):
    return [dict(r) for r in db.get_db().execute(
        """SELECT e.id, e.mode, e.recipient, e.subject, e.status, e.error, e.sent_at, u.full_name AS sent_by_name
           FROM claim_emails e LEFT JOIN users u ON u.id = e.sent_by WHERE e.incident_id = ? ORDER BY e.id DESC""",
        (iid,))]
