"""Protecciones básicas para exponer la app en internet.

- Límite de intentos de login fallidos (en memoria: se reinicia al reiniciar el servidor).
- Cabeceras de seguridad HTTP.
"""
import threading
import time
from collections import defaultdict, deque

WINDOW = 15 * 60          # ventana de 15 minutos
MAX_PER_USER = 5          # fallos por IP + usuario
MAX_PER_IP = 20           # fallos por IP (para quien prueba muchos usuarios)

_fails = defaultdict(deque)
_lock = threading.Lock()


def _keys(ip, username):
    return (f"u|{ip}|{(username or '').strip().lower()}", MAX_PER_USER), (f"ip|{ip}", MAX_PER_IP)


def _prune(q, now):
    while q and now - q[0] > WINDOW:
        q.popleft()


def seconds_blocked(ip, username):
    """0 si puede intentarlo; si no, segundos que faltan para poder volver a intentarlo."""
    now = time.time()
    with _lock:
        wait = 0
        for key, limit in _keys(ip, username):
            q = _fails[key]
            _prune(q, now)
            if len(q) >= limit:
                wait = max(wait, int(WINDOW - (now - q[0])) + 1)
        return wait


def register_failure(ip, username):
    now = time.time()
    with _lock:
        for key, _limit in _keys(ip, username):
            _fails[key].append(now)


def register_success(ip, username):
    with _lock:
        _fails.pop(_keys(ip, username)[0][0], None)


def blocked_message(seconds):
    minutes = max(1, round(seconds / 60))
    return f"Demasiados intentos fallidos. Vuelve a intentarlo en {minutes} minuto{'s' if minutes != 1 else ''}."


def add_security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    return resp


def reset_for_tests():
    with _lock:
        _fails.clear()
