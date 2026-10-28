"""Acceso a SQLite, esquema y datos de demostración."""
import random
import sqlite3
from datetime import datetime, timedelta

import click
from flask import current_app, g
from werkzeug.security import check_password_hash, generate_password_hash

# Contraseñas iniciales: se obliga a cambiarlas en el primer acceso.
DEFAULT_PASSWORDS = ("admin123", "gerente123")

from constants import DT_FMT, INCIDENT_TYPES, PLATFORMS, PRIORITIES

SCHEMA = """
CREATE TABLE IF NOT EXISTS restaurants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    full_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'gerente')),
    restaurant_id INTEGER REFERENCES restaurants(id),
    must_change_password INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    restaurant_id INTEGER NOT NULL REFERENCES restaurants(id),
    platform TEXT NOT NULL,
    order_ref TEXT NOT NULL,
    order_date TEXT NOT NULL,
    incident_type TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'media',
    description TEXT,
    items_affected TEXT,
    order_amount REAL NOT NULL DEFAULT 0,
    claimed_amount REAL NOT NULL DEFAULT 0,
    recovered_amount REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'abierta',
    platform_claim_ref TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    claimed_at TEXT,
    closed_at TEXT,
    import_batch_id INTEGER REFERENCES import_batches(id) ON DELETE SET NULL,
    source_data TEXT  -- JSON con los datos originales del archivo importado
);
CREATE INDEX IF NOT EXISTS idx_incidents_restaurant ON incidents(restaurant_id);
CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status);
CREATE INDEX IF NOT EXISTS idx_incidents_order_date ON incidents(order_date);
CREATE INDEX IF NOT EXISTS idx_incidents_order_ref ON incidents(platform, order_ref);

CREATE TABLE IF NOT EXISTS incident_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    user_id INTEGER REFERENCES users(id),
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Cada archivo Excel/CSV importado desde un portal de delivery
CREATE TABLE IF NOT EXISTS import_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    restaurant_id INTEGER REFERENCES restaurants(id),
    filename TEXT NOT NULL,
    total_rows INTEGER NOT NULL DEFAULT 0,
    created_count INTEGER NOT NULL DEFAULT 0,
    duplicate_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    profile TEXT,
    user_id INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL
);

-- Cómo llama cada plataforma a nuestros locales (se recuerda tras la primera importación)
CREATE TABLE IF NOT EXISTS restaurant_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    ext_key TEXT NOT NULL,
    ext_name TEXT,
    restaurant_id INTEGER NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
    UNIQUE (platform, ext_key)
);

-- Correos de reclamación: uno por pedido (mode 'prueba' = enviado a la dirección de prueba)
CREATE TABLE IF NOT EXISTS claim_emails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    mode TEXT NOT NULL CHECK (mode IN ('prueba', 'real')),
    recipient TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('enviado', 'error')),
    error TEXT,
    sent_by INTEGER REFERENCES users(id),
    sent_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_claim_emails_incident ON claim_emails(incident_id);
"""


def _migrate(conn):
    """Migraciones ligeras para bases de datos creadas con versiones anteriores."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(incidents)")}
    if "import_batch_id" not in cols:
        conn.execute("ALTER TABLE incidents ADD COLUMN import_batch_id INTEGER REFERENCES import_batches(id)")
    if "source_data" not in cols:
        conn.execute("ALTER TABLE incidents ADD COLUMN source_data TEXT")
    bcols = {r[1] for r in conn.execute("PRAGMA table_info(import_batches)")}
    if "skipped_count" not in bcols:
        conn.execute("ALTER TABLE import_batches ADD COLUMN skipped_count INTEGER NOT NULL DEFAULT 0")
    if "profile" not in bcols:
        conn.execute("ALTER TABLE import_batches ADD COLUMN profile TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_incidents_batch ON incidents(import_batch_id)")
    ucols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
    if "must_change_password" not in ucols:
        conn.execute("ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0")
    # Quien siga con una contraseña de fábrica tiene que cambiarla antes de usar la app
    for u in conn.execute("SELECT id, password_hash FROM users WHERE must_change_password = 0").fetchall():
        if any(check_password_hash(u["password_hash"], p) for p in DEFAULT_PASSWORDS):
            conn.execute("UPDATE users SET must_change_password = 1 WHERE id = ?", (u["id"],))


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db(reset=False, demo=True):
    conn = get_db()
    if reset:
        conn.executescript(
            "DROP TABLE IF EXISTS claim_emails;"
            "DROP TABLE IF EXISTS incident_events;"
            "DROP TABLE IF EXISTS incidents;"
            "DROP TABLE IF EXISTS import_batches;"
            "DROP TABLE IF EXISTS restaurant_aliases;"
            "DROP TABLE IF EXISTS users;"
            "DROP TABLE IF EXISTS restaurants;"
        )
    conn.executescript(SCHEMA)
    _migrate(conn)
    # Solo se siembra una base de datos nueva (sin usuarios). Así una base vacía a propósito
    # (sin locales ni incidencias) no se vuelve a llenar al arrancar.
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        _seed(conn, demo=demo)
        _migrate(conn)  # marca los usuarios recién creados para que cambien la contraseña
    conn.commit()


def init_app(app):
    app.teardown_appcontext(close_db)

    @app.cli.command("reset-password")
    @click.argument("username", default="admin")
    def reset_password_command(username):
        """Pone una contraseña temporal aleatoria (hay que cambiarla al entrar)."""
        import secrets
        conn = get_db()
        if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone() is None:
            click.echo(f"No existe el usuario «{username}».")
            return
        temp = "Temp-" + secrets.token_urlsafe(9)
        conn.execute("UPDATE users SET password_hash = ?, must_change_password = 1 WHERE username = ?",
                     (generate_password_hash(temp), username))
        conn.commit()
        click.echo(f"Contraseña temporal de «{username}»: {temp}")
        click.echo("Al entrar, la app te pedirá cambiarla.")

    @app.cli.command("reset-db")
    @click.option("--sin-demo", is_flag=True,
                  help="Base vacía: solo el usuario admin, sin locales ni incidencias de ejemplo.")
    def reset_db_command(sin_demo):
        """Borra y recrea la base de datos."""
        init_db(reset=True, demo=not sin_demo)
        click.echo("Base de datos recreada" + (" vacía (solo usuario admin)." if sin_demo else " con datos de demostración."))


# --------------------------------------------------------------------------- #
# Datos de demostración
# --------------------------------------------------------------------------- #
_ITEMS = [
    "Big Mac", "McNuggets 9 uds", "Patatas medianas", "McFlurry Oreo",
    "Coca-Cola mediana", "Cuarto de Libra", "McPollo", "Happy Meal",
    "Salsa barbacoa", "Ensalada César", "McWrap", "Sundae chocolate",
]

_DESCRIPTIONS = {
    "producto_faltante": "El cliente indica que faltaba producto en la bolsa.",
    "pedido_incorrecto": "El cliente recibió productos de otro pedido.",
    "pedido_danado": "La bebida llegó derramada y la bolsa mojada.",
    "retraso_rider": "El rider tardó más de 25 min en recoger el pedido.",
    "pedido_no_recogido": "Pedido preparado que el rider nunca recogió.",
    "cancelacion": "La plataforma canceló el pedido con la comida ya preparada.",
    "reembolso_injustificado": "Reembolso al cliente pese a que el pedido salió completo y verificado.",
    "cargo_plataforma": "La plataforma descontó un cargo operativo por este pedido.",
    "otro": "Incidencia variada, ver comentarios.",
}


def _order_ref(rng, platform):
    if platform == "glovo":
        return f"GLV-{rng.randint(10_000_000, 99_999_999)}"
    if platform == "ubereats":
        return "UE-" + "".join(rng.choice("0123456789ABCDEF") for _ in range(6))
    return f"JE-{rng.randint(100_000_000, 999_999_999)}"


def _seed(conn, demo=True):
    if not demo:
        # Base real vacía: solo un administrador para poder entrar.
        # Los locales se crean al importar el primer Excel de cada plataforma.
        conn.execute(
            "INSERT INTO users (username, password_hash, full_name, role, restaurant_id) VALUES (?, ?, ?, ?, ?)",
            ("admin", generate_password_hash("admin123"), "Administrador", "admin", None),
        )
        return

    restaurants = [
        ("MAD-001", "McDonald's Plenilunio"),
        ("MAD-002", "McDonald's Castellana"),
        ("BCN-001", "McDonald's Diagonal"),
    ]
    conn.executemany("INSERT INTO restaurants (code, name) VALUES (?, ?)", restaurants)

    conn.execute(
        "INSERT INTO users (username, password_hash, full_name, role, restaurant_id) VALUES (?, ?, ?, ?, ?)",
        ("admin", generate_password_hash("admin123"), "Administrador", "admin", None),
    )
    conn.execute(
        "INSERT INTO users (username, password_hash, full_name, role, restaurant_id) VALUES (?, ?, ?, ?, ?)",
        ("gerente", generate_password_hash("gerente123"), "Gerente Plenilunio", "gerente", 1),
    )

    if not demo:
        return

    rng = random.Random(42)
    now = datetime.now()
    for _ in range(90):
        platform = rng.choice(list(PLATFORMS))
        itype = rng.choice(list(INCIDENT_TYPES))
        restaurant_id = rng.choice([1, 1, 2, 3])
        order_dt = now - timedelta(days=rng.randint(0, 45), hours=rng.randint(0, 12), minutes=rng.randint(0, 59))
        created_dt = order_dt + timedelta(minutes=rng.randint(10, 90))
        if created_dt > now:
            created_dt = now
        order_amount = round(rng.uniform(8, 45), 2)
        if itype in ("pedido_no_recogido", "cancelacion", "reembolso_injustificado"):
            claimed = order_amount
        elif itype == "retraso_rider":
            claimed = round(rng.uniform(0, 4), 2)
        else:
            claimed = round(order_amount * rng.uniform(0.15, 0.6), 2)

        age_days = (now - order_dt).days
        if age_days < 2:
            status = rng.choice(["abierta", "abierta", "reclamada"])
        else:
            status = rng.choices(["abierta", "reclamada", "resuelta", "rechazada"], weights=[1, 3, 6, 2])[0]

        claimed_dt = closed_dt = None
        if status != "abierta":
            claimed_dt = min(created_dt + timedelta(hours=rng.randint(1, 24)), now)
        if status in ("resuelta", "rechazada"):
            closed_dt = claimed_dt + timedelta(days=rng.randint(1, 4), hours=rng.randint(0, 8))
            if closed_dt > now:
                status, closed_dt = "reclamada", None

        recovered = 0.0
        if status == "resuelta":
            recovered = claimed if rng.random() < 0.75 else round(claimed * rng.uniform(0.4, 0.9), 2)

        items = ", ".join(rng.sample(_ITEMS, rng.randint(1, 2))) if itype in (
            "producto_faltante", "pedido_incorrecto", "pedido_danado") else ""
        claim_ref = f"TK-{rng.randint(100000, 999999)}" if claimed_dt else None
        updated_dt = closed_dt or claimed_dt or created_dt
        user_id = rng.choice([1, 2])

        cur = conn.execute(
            """INSERT INTO incidents (restaurant_id, platform, order_ref, order_date, incident_type, priority,
                   description, items_affected, order_amount, claimed_amount, recovered_amount, status,
                   platform_claim_ref, created_by, created_at, updated_at, claimed_at, closed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                restaurant_id, platform, _order_ref(rng, platform), order_dt.strftime(DT_FMT), itype,
                rng.choice(list(PRIORITIES)), _DESCRIPTIONS[itype], items, order_amount, claimed, recovered,
                status, claim_ref, user_id, created_dt.strftime(DT_FMT), updated_dt.strftime(DT_FMT),
                claimed_dt.strftime(DT_FMT) if claimed_dt else None,
                closed_dt.strftime(DT_FMT) if closed_dt else None,
            ),
        )
        iid = cur.lastrowid
        events = [(iid, user_id, "creada", "Incidencia registrada.", created_dt)]
        if claimed_dt:
            events.append((iid, user_id, "estado", f"Estado: Abierta → Reclamada a plataforma (ticket {claim_ref}).", claimed_dt))
        if status == "resuelta":
            events.append((iid, user_id, "estado", f"Estado: Reclamada a plataforma → Resuelta. Recuperado: {recovered:.2f} €.", closed_dt))
        elif status == "rechazada":
            events.append((iid, user_id, "estado", "Estado: Reclamada a plataforma → Rechazada. La plataforma no acepta la reclamación.", closed_dt))
        conn.executemany(
            "INSERT INTO incident_events (incident_id, user_id, event_type, message, created_at) VALUES (?, ?, ?, ?, ?)",
            [(a, b, c, d, e.strftime(DT_FMT)) for a, b, c, d, e in events],
        )
