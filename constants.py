"""Catálogos compartidos de la aplicación."""

DT_FMT = "%Y-%m-%d %H:%M:%S"

PLATFORMS = {
    "glovo": {"name": "Glovo", "color": "#FFC244"},
    "ubereats": {"name": "Uber Eats", "color": "#06C167"},
    "justeat": {"name": "Just Eat", "color": "#FF8000"},
}

INCIDENT_TYPES = {
    "producto_faltante": "Producto faltante",
    "pedido_incorrecto": "Pedido incorrecto",
    "pedido_danado": "Pedido dañado / derramado",
    "retraso_rider": "Retraso del rider",
    "pedido_no_recogido": "Pedido no recogido",
    "cancelacion": "Cancelación tras preparación",
    "reembolso_injustificado": "Reembolso injustificado al cliente",
    "cargo_plataforma": "Cargo / penalización de la plataforma",
    "otro": "Otro",
}

STATUSES = {
    "abierta": "Abierta",
    "reclamada": "Reclamada a plataforma",
    "resuelta": "Resuelta",
    "rechazada": "Rechazada",
}
OPEN_STATUSES = ("abierta", "reclamada")
CLOSED_STATUSES = ("resuelta", "rechazada")

PRIORITIES = {"baja": "Baja", "media": "Media", "alta": "Alta"}

# Días a partir de los cuales una incidencia pendiente se marca como "requiere atención".
# Ajustar según los plazos reales de reclamación que tenga cada plataforma en contrato.
ALERT_DAYS = 3
