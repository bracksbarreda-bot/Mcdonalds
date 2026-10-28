"""Lógica de negocio compartida por la web clásica (app.py) y la API REST (api.py).

Todas las funciones asumen que `g.user` ya está cargado (fila de users + restaurant_name).
"""
import csv
import io
import json
import math
from datetime import date, datetime, timedelta

from flask import g

import db
from constants import (ALERT_DAYS, CLOSED_STATUSES, DT_FMT, INCIDENT_TYPES,
                       OPEN_STATUSES, PLATFORMS, PRIORITIES, STATUSES)

PER_PAGE = 25


# --------------------------------------------------------------------------- #
# Formato y parsing
# --------------------------------------------------------------------------- #
def now_str():
    return datetime.now().strftime(DT_FMT)


def parse_money(raw, field):
    """Convierte '12,50', '1.234,50', '12.5' o un número en float. Devuelve (valor, error)."""
    if isinstance(raw, (int, float)):
        raw = str(raw)
    raw = (raw or "").strip().replace("€", "").replace(" ", "")
    if raw == "":
        return 0.0, None
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    try:
        value = round(float(raw), 2)
    except ValueError:
        return 0.0, f"{field}: importe no válido."
    if math.isnan(value) or value < 0 or value > 100_000:
        return 0.0, f"{field}: importe fuera de rango."
    return value, None


def fmt_money(value):
    s = f"{(value or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} €"


def fmt_dt(value, fmt="%d/%m/%Y %H:%M"):
    if not value:
        return "—"
    try:
        return datetime.strptime(value, DT_FMT).strftime(fmt)
    except ValueError:
        return value


def days_since(value):
    try:
        return (datetime.now() - datetime.strptime(value, DT_FMT)).days
    except (TypeError, ValueError):
        return 0


# --------------------------------------------------------------------------- #
# Usuario y permisos
# --------------------------------------------------------------------------- #
def load_user(uid):
    return db.get_db().execute(
        """SELECT u.*, r.name AS restaurant_name FROM users u
           LEFT JOIN restaurants r ON r.id = u.restaurant_id WHERE u.id = ?""",
        (uid,),
    ).fetchone()


def user_public(user):
    return {
        "id": user["id"], "username": user["username"], "full_name": user["full_name"],
        "role": user["role"], "restaurant_id": user["restaurant_id"], "restaurant_name": user["restaurant_name"],
        "must_change_password": bool(user["must_change_password"]),
    }


def password_version(user):
    """Huella del hash actual: si cambia la contraseña, las sesiones/tokens anteriores dejan de valer."""
    return user["password_hash"][-16:]


def change_password(user, current, new, confirm):
    """Cambia la contraseña del usuario. Devuelve un mensaje de error o None si se cambió."""
    from werkzeug.security import check_password_hash, generate_password_hash

    current, new, confirm = str(current or ""), str(new or ""), str(confirm or "")
    if not check_password_hash(user["password_hash"], current):
        return "La contraseña actual no es correcta."
    if new != confirm:
        return "Las dos contraseñas nuevas no coinciden."
    if len(new) < 10:
        return "La contraseña nueva debe tener al menos 10 caracteres."
    if len(new) > 128:
        return "La contraseña nueva es demasiado larga."
    if new.lower() in (user["username"].lower(), "admin123", "gerente123", "contraseña", "password12") or new == current:
        return "Elige una contraseña distinta y menos previsible."
    if not (any(c.isalpha() for c in new) and any(c.isdigit() for c in new)):
        return "La contraseña nueva debe combinar letras y números."
    conn = db.get_db()
    conn.execute("UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?",
                 (generate_password_hash(new), user["id"]))
    conn.commit()
    return None


def is_admin():
    return g.get("user") is not None and g.user["role"] == "admin"


def scope_clause(alias="i"):
    """Restringe las consultas al local del gerente; el admin ve todo."""
    if is_admin():
        return "", []
    return f" AND {alias}.restaurant_id = ?", [g.user["restaurant_id"]]


def get_restaurants():
    conn = db.get_db()
    if is_admin():
        return conn.execute("SELECT * FROM restaurants ORDER BY name").fetchall()
    return conn.execute("SELECT * FROM restaurants WHERE id = ?", (g.user["restaurant_id"],)).fetchall()


def add_event(incident_id, event_type, message):
    db.get_db().execute(
        "INSERT INTO incident_events (incident_id, user_id, event_type, message, created_at) VALUES (?, ?, ?, ?, ?)",
        (incident_id, g.user["id"], event_type, message, now_str()),
    )


def get_incident(iid):
    """Devuelve la incidencia si existe y el usuario tiene acceso; si no, None."""
    row = db.get_db().execute(
        """SELECT i.*, r.name AS restaurant_name, r.code AS restaurant_code, u.full_name AS created_by_name
           FROM incidents i
           JOIN restaurants r ON r.id = i.restaurant_id
           LEFT JOIN users u ON u.id = i.created_by
           WHERE i.id = ?""",
        (iid,),
    ).fetchone()
    if row is None or (not is_admin() and row["restaurant_id"] != g.user["restaurant_id"]):
        return None
    return row


def get_events(iid):
    return db.get_db().execute(
        """SELECT e.*, u.full_name FROM incident_events e LEFT JOIN users u ON u.id = e.user_id
           WHERE e.incident_id = ? ORDER BY e.created_at DESC, e.id DESC""",
        (iid,),
    ).fetchall()


# --------------------------------------------------------------------------- #
# Panel
# --------------------------------------------------------------------------- #
def dashboard_data(days):
    if days not in (7, 30, 90):
        days = 30
    conn = db.get_db()
    now = datetime.now()
    since = (now - timedelta(days=days)).strftime(DT_FMT)
    sc, sp = scope_clause()
    params = [since, *sp]
    open_list = ",".join(f"'{s}'" for s in OPEN_STATUSES)
    closed_list = ",".join(f"'{s}'" for s in CLOSED_STATUSES)

    totals = dict(conn.execute(
        f"""SELECT COUNT(*) AS total,
                   COALESCE(SUM(status IN ({open_list})), 0) AS pending,
                   COALESCE(SUM(claimed_amount), 0) AS claimed,
                   COALESCE(SUM(recovered_amount), 0) AS recovered,
                   COALESCE(SUM(CASE WHEN status IN ({closed_list}) THEN claimed_amount END), 0) AS closed_claimed,
                   COALESCE(SUM(CASE WHEN status IN ({open_list}) THEN claimed_amount END), 0) AS pending_amount
            FROM incidents i WHERE i.order_date >= ?{sc}""",
        params,
    ).fetchone())
    rate = round(totals["recovered"] / totals["closed_claimed"] * 100, 1) if totals["closed_claimed"] else 0

    rows = {
        r["platform"]: r
        for r in conn.execute(
            f"""SELECT platform, COUNT(*) AS total,
                       COALESCE(SUM(status IN ({open_list})), 0) AS pending,
                       COALESCE(SUM(claimed_amount), 0) AS claimed,
                       COALESCE(SUM(recovered_amount), 0) AS recovered
                FROM incidents i WHERE i.order_date >= ?{sc} GROUP BY platform""",
            params,
        )
    }
    by_platform = []
    for key, meta in PLATFORMS.items():
        r = rows.get(key)
        by_platform.append({
            "key": key, "name": meta["name"], "color": meta["color"],
            "total": r["total"] if r else 0, "pending": r["pending"] if r else 0,
            "claimed": r["claimed"] if r else 0, "recovered": r["recovered"] if r else 0,
        })

    by_type = [dict(r) for r in conn.execute(
        f"""SELECT incident_type, COUNT(*) AS n FROM incidents i
            WHERE i.order_date >= ?{sc} GROUP BY incident_type ORDER BY n DESC""",
        params,
    )]
    max_type = max((t["n"] for t in by_type), default=1)

    chart_days = min(days, 30)
    chart_since = (now - timedelta(days=chart_days - 1)).strftime("%Y-%m-%d")
    counts = {
        r["d"]: r["n"]
        for r in conn.execute(
            f"""SELECT substr(order_date, 1, 10) AS d, COUNT(*) AS n FROM incidents i
                WHERE i.order_date >= ?{sc} GROUP BY d""",
            [chart_since, *sp],
        )
    }
    daily = []
    for k in range(chart_days):
        d = (now - timedelta(days=chart_days - 1 - k)).date()
        daily.append({"date": d.isoformat(), "label": d.strftime("%d/%m"), "n": counts.get(d.isoformat(), 0)})
    max_daily = max((d["n"] for d in daily), default=1) or 1

    alert_before = (now - timedelta(days=ALERT_DAYS)).strftime(DT_FMT)
    alerts = [dict(r) for r in conn.execute(
        f"""SELECT i.*, r.name AS restaurant_name FROM incidents i
            JOIN restaurants r ON r.id = i.restaurant_id
            WHERE i.status IN ({open_list}) AND i.order_date <= ?{sc}
            ORDER BY i.order_date ASC LIMIT 10""",
        [alert_before, *sp],
    )]
    for a in alerts:
        a["days"] = days_since(a["order_date"])

    by_restaurant = []
    if is_admin():
        by_restaurant = [dict(r) for r in conn.execute(
            """SELECT r.id, r.name, COUNT(i.id) AS total,
                      COALESCE(SUM(i.claimed_amount), 0) AS claimed,
                      COALESCE(SUM(i.recovered_amount), 0) AS recovered
               FROM restaurants r
               LEFT JOIN incidents i ON i.restaurant_id = r.id AND i.order_date >= ?
               GROUP BY r.id ORDER BY total DESC""",
            (since,),
        )]

    return {
        "days": days, "totals": totals, "rate": rate, "by_platform": by_platform, "by_type": by_type,
        "max_type": max_type, "daily": daily, "max_daily": max_daily, "alerts": alerts,
        "by_restaurant": by_restaurant,
    }


# --------------------------------------------------------------------------- #
# Listado, filtros y CSV
# --------------------------------------------------------------------------- #
def build_filters(args):
    where, params = ["1=1"], []
    sc, sp = scope_clause()
    if sc:
        where.append(sc.removeprefix(" AND "))
        params += sp

    platform = args.get("platform", "")
    if platform in PLATFORMS:
        where.append("i.platform = ?")
        params.append(platform)

    status = args.get("status", "")
    if status == "pendientes":
        where.append(f"i.status IN ({','.join('?' * len(OPEN_STATUSES))})")
        params += list(OPEN_STATUSES)
    elif status in STATUSES:
        where.append("i.status = ?")
        params.append(status)

    itype = args.get("type", "")
    if itype in INCIDENT_TYPES:
        where.append("i.incident_type = ?")
        params.append(itype)

    if is_admin() and str(args.get("restaurant", "")).isdigit():
        where.append("i.restaurant_id = ?")
        params.append(int(args["restaurant"]))

    if str(args.get("batch", "")).isdigit():
        where.append("i.import_batch_id = ?")
        params.append(int(args["batch"]))

    q = str(args.get("q", "")).strip()[:100]
    if q:
        where.append("(i.order_ref LIKE ? OR i.description LIKE ? OR i.items_affected LIKE ? OR i.platform_claim_ref LIKE ?)")
        params += [f"%{q}%"] * 4

    for key, op, offset in (("desde", ">=", 0), ("hasta", "<", 1)):
        raw = args.get(key, "")
        if raw:
            try:
                d = date.fromisoformat(raw) + timedelta(days=offset)
            except ValueError:
                continue
            where.append(f"i.order_date {op} ?")
            params.append(d.isoformat())

    return " AND ".join(where), params


def list_incidents(args, page=1):
    conn = db.get_db()
    where, params = build_filters(args)
    total = conn.execute(f"SELECT COUNT(*) FROM incidents i WHERE {where}", params).fetchone()[0]
    pages = max(1, math.ceil(total / PER_PAGE))
    page = min(max(1, page), pages)
    rows = conn.execute(
        f"""SELECT i.*, r.name AS restaurant_name FROM incidents i
            JOIN restaurants r ON r.id = i.restaurant_id
            WHERE {where} ORDER BY i.order_date DESC, i.id DESC LIMIT ? OFFSET ?""",
        [*params, PER_PAGE, (page - 1) * PER_PAGE],
    ).fetchall()
    sums = dict(conn.execute(
        f"SELECT COALESCE(SUM(claimed_amount),0) AS claimed, COALESCE(SUM(recovered_amount),0) AS recovered "
        f"FROM incidents i WHERE {where}",
        params,
    ).fetchone())
    return {"rows": rows, "total": total, "page": page, "pages": pages, "sums": sums}


def _csv_safe(value):
    """Evita inyección de fórmulas al abrir el CSV en Excel."""
    s = "" if value is None else str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def export_csv_text(args):
    where, params = build_filters(args)
    rows = db.get_db().execute(
        f"""SELECT i.*, r.name AS restaurant_name, r.code AS restaurant_code FROM incidents i
            JOIN restaurants r ON r.id = i.restaurant_id
            WHERE {where} ORDER BY i.order_date DESC""",
        params,
    ).fetchall()

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow([
        "ID", "Fecha pedido", "Código local", "Local", "Plataforma", "Nº pedido", "Tipo", "Prioridad",
        "Estado", "Productos afectados", "Descripción", "Importe pedido", "Importe reclamado",
        "Importe recuperado", "Ticket plataforma", "Registrada", "Reclamada", "Cerrada",
    ])
    money = lambda v: f"{(v or 0):.2f}".replace(".", ",")
    for r in rows:
        writer.writerow([_csv_safe(v) for v in (
            r["id"], r["order_date"], r["restaurant_code"], r["restaurant_name"],
            PLATFORMS[r["platform"]]["name"], r["order_ref"], INCIDENT_TYPES.get(r["incident_type"], r["incident_type"]),
            PRIORITIES.get(r["priority"], r["priority"]), STATUSES.get(r["status"], r["status"]),
            r["items_affected"], r["description"], money(r["order_amount"]), money(r["claimed_amount"]),
            money(r["recovered_amount"]), r["platform_claim_ref"], r["created_at"], r["claimed_at"], r["closed_at"],
        )])
    return "\ufeff" + buf.getvalue()  # BOM para que Excel detecte UTF-8


# --------------------------------------------------------------------------- #
# Alta / edición / estado
# --------------------------------------------------------------------------- #
def parse_incident_form(form):
    """Valida datos de alta/edición. `form` puede ser request.form o un dict JSON."""
    get = lambda k, d="": ("" if form.get(k) is None else str(form.get(k, d)))
    errors, data = [], {}

    data["platform"] = get("platform")
    if data["platform"] not in PLATFORMS:
        errors.append("Selecciona una plataforma.")

    data["order_ref"] = get("order_ref").strip()
    if not data["order_ref"]:
        errors.append("El número de pedido es obligatorio.")
    elif len(data["order_ref"]) > 64:
        errors.append("El número de pedido es demasiado largo.")

    try:
        data["order_date"] = datetime.fromisoformat(get("order_date")).replace(tzinfo=None).strftime(DT_FMT)
        if data["order_date"] > now_str():
            errors.append("La fecha del pedido no puede ser futura.")
    except ValueError:
        errors.append("Fecha del pedido no válida.")

    data["incident_type"] = get("incident_type")
    if data["incident_type"] not in INCIDENT_TYPES:
        errors.append("Selecciona un tipo de incidencia.")

    data["priority"] = get("priority", "media")
    if data["priority"] not in PRIORITIES:
        data["priority"] = "media"

    data["items_affected"] = get("items_affected").strip()[:500]
    data["description"] = get("description").strip()[:2000]
    data["platform_claim_ref"] = get("platform_claim_ref").strip()[:64] or None

    data["order_amount"], err = parse_money(form.get("order_amount"), "Importe del pedido")
    if err:
        errors.append(err)
    data["claimed_amount"], err = parse_money(form.get("claimed_amount"), "Importe a reclamar")
    if err:
        errors.append(err)
    if not errors and data["claimed_amount"] > data["order_amount"]:
        errors.append("El importe a reclamar no puede superar el importe del pedido.")

    if is_admin():
        rid = get("restaurant_id")
        valid_ids = {r["id"] for r in get_restaurants()}
        if not rid.isdigit() or int(rid) not in valid_ids:
            errors.append("Selecciona un restaurante.")
        else:
            data["restaurant_id"] = int(rid)
    else:
        data["restaurant_id"] = g.user["restaurant_id"]

    return data, errors


def find_duplicate(platform, order_ref, restaurant_id, exclude_id=None):
    row = db.get_db().execute(
        "SELECT id FROM incidents WHERE platform = ? AND order_ref = ? AND restaurant_id = ? AND id != ?",
        (platform, order_ref, restaurant_id, exclude_id or 0),
    ).fetchone()
    return row["id"] if row else None


def create_incident(data, event_msg="Incidencia registrada.", batch_id=None, commit=True, source_data=None):
    """source_data: lista [[etiqueta, valor], ...] con los datos originales del archivo importado."""
    conn = db.get_db()
    ts = now_str()
    cur = conn.execute(
        """INSERT INTO incidents (restaurant_id, platform, order_ref, order_date, incident_type, priority,
               description, items_affected, order_amount, claimed_amount, platform_claim_ref, status,
               created_by, created_at, updated_at, import_batch_id, source_data)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'abierta', ?, ?, ?, ?, ?)""",
        (data["restaurant_id"], data["platform"], data["order_ref"], data["order_date"], data["incident_type"],
         data.get("priority", "media"), data.get("description", ""), data.get("items_affected", ""),
         data.get("order_amount", 0), data.get("claimed_amount", 0), data.get("platform_claim_ref"),
         g.user["id"], ts, ts, batch_id, json.dumps(source_data, ensure_ascii=False) if source_data else None),
    )
    add_event(cur.lastrowid, "creada", event_msg)
    if commit:
        conn.commit()
    return cur.lastrowid


def update_incident(inc, data):
    """Devuelve lista de errores (vacía si se guardó)."""
    if data["claimed_amount"] < inc["recovered_amount"]:
        return ["El importe reclamado no puede ser menor que lo ya recuperado."]
    conn = db.get_db()
    conn.execute(
        """UPDATE incidents SET restaurant_id = ?, platform = ?, order_ref = ?, order_date = ?,
               incident_type = ?, priority = ?, description = ?, items_affected = ?, order_amount = ?,
               claimed_amount = ?, platform_claim_ref = ?, updated_at = ?
           WHERE id = ?""",
        (data["restaurant_id"], data["platform"], data["order_ref"], data["order_date"], data["incident_type"],
         data["priority"], data["description"], data["items_affected"], data["order_amount"],
         data["claimed_amount"], data["platform_claim_ref"], now_str(), inc["id"]),
    )
    add_event(inc["id"], "edicion", "Datos de la incidencia actualizados.")
    conn.commit()
    return []


def change_status(inc, form):
    """Aplica un cambio de estado. Devuelve (ok, mensaje, nivel) con nivel success/warning/error."""
    get = lambda k: "" if form.get(k) is None else str(form.get(k))
    new = get("status")
    comment = get("comment").strip()[:1000]
    if new not in STATUSES:
        return False, "Estado no válido.", "error"

    ts = now_str()
    fields = {"status": new, "updated_at": ts}
    msg = f"Estado: {STATUSES[inc['status']]} → {STATUSES[new]}"

    claim_ref = get("platform_claim_ref").strip()[:64]
    if claim_ref:
        fields["platform_claim_ref"] = claim_ref

    if new == "reclamada":
        fields.update(claimed_at=inc["claimed_at"] or ts, closed_at=None, recovered_amount=0)
        if claim_ref:
            msg += f" (ticket {claim_ref})"
    elif new == "resuelta":
        recovered, err = parse_money(form.get("recovered_amount"), "Importe recuperado")
        if err or recovered > inc["order_amount"]:
            return False, err or "El importe recuperado no puede superar el importe del pedido.", "error"
        fields.update(recovered_amount=recovered, closed_at=ts, claimed_at=inc["claimed_at"] or ts)
        msg += f". Recuperado: {fmt_money(recovered)}"
    elif new == "rechazada":
        fields.update(recovered_amount=0, closed_at=ts)
    else:  # abierta
        fields.update(recovered_amount=0, closed_at=None)

    ref_changed = bool(claim_ref) and claim_ref != (inc["platform_claim_ref"] or "")
    if new == inc["status"] and new != "resuelta" and not ref_changed and not comment:
        return False, "La incidencia ya estaba en ese estado.", "warning"

    sets = ", ".join(f"{k} = ?" for k in fields)  # claves fijas definidas arriba, no vienen del usuario
    conn = db.get_db()
    conn.execute(f"UPDATE incidents SET {sets} WHERE id = ?", [*fields.values(), inc["id"]])
    add_event(inc["id"], "estado", msg + (f". {comment}" if comment else "."))
    conn.commit()
    return True, "Estado actualizado.", "success"


def add_comment(inc, text):
    text = (text or "").strip()[:1000]
    if not text:
        return False
    conn = db.get_db()
    add_event(inc["id"], "comentario", text)
    conn.execute("UPDATE incidents SET updated_at = ? WHERE id = ?", (now_str(), inc["id"]))
    conn.commit()
    return True


def delete_incident(iid):
    conn = db.get_db()
    conn.execute("DELETE FROM incidents WHERE id = ?", (iid,))
    conn.commit()


def build_claim_text(inc):
    lines = [
        f"Hola equipo de soporte de {PLATFORMS[inc['platform']]['name']}:",
        "",
        f"Desde {inc['restaurant_name']} ({inc['restaurant_code']}) solicitamos revisar el pedido "
        f"{inc['order_ref']} del {fmt_dt(inc['order_date'])}.",
        f"Motivo: {INCIDENT_TYPES[inc['incident_type']]}.",
    ]
    if inc["items_affected"]:
        lines.append(f"Productos afectados: {inc['items_affected']}.")
    if inc["description"]:
        lines.append(f"Detalle: {inc['description']}")
    lines += [
        f"Importe del pedido: {fmt_money(inc['order_amount'])}.",
        f"Importe que reclamamos: {fmt_money(inc['claimed_amount'])}.",
        "",
        "Quedamos a la espera de su respuesta. Gracias.",
    ]
    return "\n".join(lines)
