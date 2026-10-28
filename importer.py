"""Lectura de los Excel de incidencias de cada plataforma.

Cada plataforma tiene SIEMPRE la misma estructura y un solo Excel con todos los restaurantes:

- Uber Eats  ("UBER ORDERS INACCURATE …"):  Order ID | Date and time | Chargebacks | <local>
- Just Eat   ("JUST ORDERS INACCURATE …"):  PEDIDOS ("20:37#192370685REFUND 26,13 €") | RTES (local)
- Glovo      ("GLOVO … ORDERS INACCURATE …"): Order received at | Order ID | Payout amount | Operational charges
             [| local]  — si no trae columna de local, se toma del nombre del archivo ("GLOVO ERMITA …").

La plataforma se reconoce sola por las columnas. El local de cada fila sale del propio Excel
y se crea automáticamente la primera vez que aparece.
"""
import re
import unicodedata
from datetime import date, datetime, timedelta

MAX_ROWS = 5000

PROFILES = {
    "uber_inaccurate": {
        "platform": "ubereats",
        "label": "Uber Eats · Pedidos inexactos",
        "signature": {"order id", "date and time", "chargebacks"},
        "explain": [
            "Una fila por pedido. Se reclama el <strong>chargeback</strong> (lo que Uber te descontó).",
            "El local de cada pedido es la última columna (PLAZA, BRAVO…).",
        ],
    },
    "justeat_inaccurate": {
        "platform": "justeat",
        "label": "Just Eat · Pedidos inexactos",
        "signature": {"pedidos", "rtes"},
        "needs_date": True,
        "explain": [
            "Cada celda de PEDIDOS trae hora, nº de pedido, <strong>REFUND</strong> o <strong>D</strong>, e importe.",
            "El local de cada pedido es la columna RTES.",
            "El archivo no indica el día: se usa el día de abajo para todos los pedidos.",
        ],
    },
    "glovo_inaccurate": {
        "platform": "glovo",
        "label": "Glovo · Pedidos inexactos",
        "signature": {"order received at", "order id", "payout amount", "operational charges"},
        "explain": [
            "Una fila por pedido. Se reclaman los <strong>cargos operativos</strong> que Glovo te descontó.",
            "El local sale de la columna de local; si no la hay, del nombre del archivo («GLOVO ERMITA …»).",
        ],
    },
}

_STORE_HEADERS = ("rtes", "local", "restaurante", "restaurant", "tienda", "store")
_MONTHS = {"ene": 1, "jan": 1, "feb": 2, "mar": 3, "abr": 4, "apr": 4, "may": 5, "jun": 6, "jul": 7,
           "ago": 8, "aug": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12, "dec": 12}
_FILENAME_NOISE = {"glovo", "uber", "eats", "ubereats", "just", "eat", "justeat", "orders", "order",
                   "pedidos", "inaccurate", "xlsx", "xlsm"} | set(_MONTHS)
_DATE_FORMATS = ["%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y",
                 "%b %d, %Y, %I:%M %p", "%b %d, %Y %I:%M %p"]  # Uber: "Oct 1, 2026, 2:32 PM"


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #
def normalize(text):
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _cell_str(v):
    return "" if v is None else str(v).strip()


def _id_str(v):
    """Excel guarda los IDs numéricos como 123.0."""
    return str(int(v)) if isinstance(v, float) and v.is_integer() else _cell_str(v)


def money_es(v):
    return f"{(v or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " €"


def parse_date(value):
    if isinstance(value, datetime):
        return value.replace(microsecond=0, tzinfo=None)
    if isinstance(value, (int, float)) and 20000 < value < 80000:  # nº de serie de Excel
        return (datetime(1899, 12, 30) + timedelta(days=float(value))).replace(microsecond=0)
    s = _cell_str(value)
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def parse_amount(value):
    """Importe en positivo (los descuentos vienen en negativo). None si no es un número."""
    if isinstance(value, (int, float)):
        return round(abs(float(value)), 2)
    s = re.sub(r"[^\d,.\-]", "", _cell_str(value))
    if not s.strip("-.,"):
        return 0.0
    if "," in s:  # formato español: 1.234,56
        s = s.replace(".", "").replace(",", ".")
    try:
        return round(abs(float(s)), 2)
    except ValueError:
        return None


def _source_value(v):
    if isinstance(v, datetime):
        return v.strftime("%d/%m/%Y %H:%M")
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else f"{v:.2f}".replace(".", ",")
    return _cell_str(v)[:500]


def suggest_restaurant(name):
    """«PLAZA» -> {"name": "McDonald's Plaza", "code": "PLAZA"} (mismo código en todas las plataformas)."""
    clean = re.sub(r"[®™]", "", name or "").strip()
    code = normalize(clean).upper().replace(" ", "-")[:20] or "LOCAL"
    pretty = clean.title() if clean.isupper() else clean
    return {"name": pretty if "mcdonald" in normalize(clean) else f"McDonald's {pretty}", "code": code}


def month_from_filename(filename):
    """«JUST ORDERS INACCURATE OCT 26.xlsx» -> date(2026, 10, 1)."""
    m = re.search(r"\b(" + "|".join(_MONTHS) + r")[a-z]*\s*(\d{2}|\d{4})\b", normalize(filename))
    if not m:
        return None
    year = int(m.group(2))
    return date(year + 2000 if year < 100 else year, _MONTHS[m.group(1)], 1)


def store_from_filename(filename):
    """«GLOVO ERMITA ORDERS INACCURATE OCT 26.xlsx» -> «ERMITA»."""
    return " ".join(w for w in normalize(filename).split() if w not in _FILENAME_NOISE and not w.isdigit()).upper()


# --------------------------------------------------------------------------- #
# Lectura del Excel
# --------------------------------------------------------------------------- #
def read_table(path):
    """Devuelve {"profile", "headers", "rows", "rownums"} o lanza ValueError si no es un formato conocido."""
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        rows = [list(r) for r in wb.worksheets[0].iter_rows(values_only=True, max_row=MAX_ROWS + 20)]
    finally:
        wb.close()

    # La fila de cabeceras está entre las primeras (Glovo deja la fila 1 vacía)
    for idx, row in enumerate(rows[:10]):
        names = {normalize(c) for c in row if _cell_str(c)}
        profile = next((k for k, p in PROFILES.items() if p["signature"] <= names), None)
        if profile:
            break
    else:
        raise ValueError("Este archivo no tiene la estructura de ninguno de los Excel de Uber Eats, "
                         "Just Eat o Glovo. Revisa que las columnas sean las de siempre.")

    headers = [_cell_str(c) or f"Columna {i + 1}" for i, c in enumerate(rows[idx])]
    data, nums = [], []
    for n, row in enumerate(rows[idx + 1:], start=idx + 2):
        if any(_cell_str(c) for c in row):
            data.append((list(row) + [None] * len(headers))[:len(headers)])
            nums.append(n)
    return {"profile": profile, "headers": headers, "rows": data, "rownums": nums}


# --------------------------------------------------------------------------- #
# Interpretación de cada formato
# --------------------------------------------------------------------------- #
def _store_index(headers, signature):
    norm = [normalize(h) for h in headers]
    for name in _STORE_HEADERS:
        if name in norm:
            return norm.index(name)
    extra = [i for i, h in enumerate(norm) if h not in signature and not h.startswith("columna")]
    blank = [i for i, h in enumerate(headers) if h.startswith("Columna ")]
    return (extra or blank or [None])[0]  # Uber: la columna sin título


def _item(table, row, n, *, ref, dt, claimed, store, headline, order_amount=0, warnings=(), approx=False,
          lines=(), error=None):
    warnings = list(warnings)
    if claimed is None:
        error = error or "Importe no válido"
    if dt is None:
        error = error or "Fecha no reconocida"
    if not store:
        error = error or "La fila no indica el local"
    if not error and claimed <= 0.05:
        warnings.append("Importe muy pequeño (≤ 0,05 €).")
    return {
        "row": n, "error": error, "warnings": warnings, "headline": headline, "approx_date": approx,
        "store": store.strip().upper(),
        "source": [[h, _source_value(v)] for h, v in zip(table["headers"], row) if _cell_str(v)],
        "data": None if error else {
            "order_ref": ref[:64],
            "order_date": dt.strftime("%Y-%m-%d %H:%M:%S"),
            "incident_type": "pedido_incorrecto",
            "priority": "alta" if claimed >= 10 else "media" if claimed >= 1 else "baja",
            "claimed_amount": claimed,
            "order_amount": round(max(order_amount, claimed), 2),
            "items_affected": "",
            "description": "\n".join(lines)[:2000],
            "platform_claim_ref": None,
        },
    }


def _uber(table, ctx, col, store_i):
    for row, n in zip(table["rows"], table["rownums"]):
        raw = row[col["order id"]]
        ref, warnings = _id_str(raw), []
        if not ref:
            continue
        if isinstance(raw, (int, float)) and len(ref) < 5:  # Excel convirtió "02133" en 2133
            warnings.append(f"Excel convirtió el código en número ({ref}); se completa a {ref.zfill(5)}.")
            ref = ref.zfill(5)
        claimed = parse_amount(row[col["chargebacks"]])
        yield _item(table, row, n, ref=ref, dt=parse_date(row[col["date and time"]]), claimed=claimed,
                    store=_cell_str(row[store_i]) if store_i is not None else "",
                    headline="Chargeback por pedido inexacto", warnings=warnings,
                    lines=[f"Uber Eats · Chargeback por pedido inexacto: {money_es(claimed)}."])


# "20:37#192370685REFUND 26,13 €"  ·  "0:31#192573206  D  22,59 €"  ·  "192105857 -40,60 €"
_JE_ORDER = re.compile(r"^\s*(?:(\d{1,2}):(\d{2})\s*#)?\s*(\d{9})(\S*)")
_JE_AMOUNT = re.compile(r"-?\d{1,3}(?:\.\d{3})*,\d{2}(?=\s*€)")


def _justeat(table, ctx, col, store_i):
    for row, n in zip(table["rows"], table["rownums"]):
        text = _cell_str(row[col["pedidos"]]).replace("\xa0", " ")
        if not text:
            continue
        store = _cell_str(row[store_i]) if store_i is not None else ""
        m = _JE_ORDER.match(text)
        if not m:
            yield _item(table, row, n, ref="", dt=None, claimed=0, store=store, headline=text[:60],
                        error="No se encuentra el nº de pedido (9 cifras)")
            continue
        hh, mm, ref, tail = m.groups()
        kind = "REFUND" if "REFUND" in text.upper() else "D" if re.search(r"\sD\s", f" {text} ") else ""
        warnings = []
        if tail[:1].isdigit():
            warnings.append(f"Cifras pegadas al nº de pedido («{ref}{re.match(r'[0-9,]*', tail).group()}»): revísalo.")
        if not hh:
            warnings.append("Sin hora en el archivo.")
        amounts = _JE_AMOUNT.findall(text)
        claimed = parse_amount(amounts[-1]) if amounts else None
        headline = {"REFUND": "Reembolso (REFUND)", "D": "Marcado «D»"}.get(kind, "Importe descontado")
        dt = datetime.combine(ctx["ref_date"], datetime.min.time()).replace(hour=int(hh or 0) % 24, minute=int(mm or 0))
        yield _item(table, row, n, ref=ref, dt=dt, claimed=claimed, store=store, headline=headline,
                    warnings=warnings, approx=True,
                    lines=[f"Just Eat · {headline}: {money_es(claimed)}.", f"Texto original: {text}",
                           "Fecha aproximada: el archivo no indica el día."])


def _glovo(table, ctx, col, store_i):
    file_store = store_from_filename(ctx["filename"])
    for row, n in zip(table["rows"], table["rownums"]):
        ref = _id_str(row[col["order id"]])
        if not ref:
            continue
        charges = parse_amount(row[col["operational charges"]])
        payout = parse_amount(row[col["payout amount"]]) or 0.0
        store = (_cell_str(row[store_i]) if store_i is not None else "") or file_store
        yield _item(table, row, n, ref=ref, dt=parse_date(row[col["order received at"]]), claimed=charges,
                    order_amount=payout + (charges or 0), store=store,
                    headline="Cargo operativo por pedido inexacto",
                    warnings=[] if charges else ["Sin cargo operativo en el archivo."],
                    lines=[f"Glovo · Cargo operativo descontado: {money_es(charges)}.",
                           f"Glovo liquidó {money_es(payout)} por este pedido."])


_ANALYSERS = {"uber_inaccurate": _uber, "justeat_inaccurate": _justeat, "glovo_inaccurate": _glovo}


def analyse(table, ctx):
    """ctx: {"filename": nombre original, "ref_date": día para Just Eat}. Devuelve la lista de filas."""
    signature = PROFILES[table["profile"]]["signature"]
    col = {normalize(h): i for i, h in enumerate(table["headers"])}
    store_i = _store_index(table["headers"], signature)
    return list(_ANALYSERS[table["profile"]](table, ctx, col, store_i))
