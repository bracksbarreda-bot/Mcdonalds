"""Arranca Delivery Hub con un servidor de producción (waitress) en lugar del de desarrollo de Flask.

Uso:  .venv\\Scripts\\python.exe servidor.py
  - Web clásica:        http://127.0.0.1:8080/
  - Frontend separado:  http://127.0.0.1:8080/app/
  - API:                http://127.0.0.1:8080/api/

Variables de entorno opcionales:
  HOST=127.0.0.1   (por defecto solo este equipo; el túnel se conecta aquí)
  PORT=8080
  TRUST_PROXY=1    si va detrás de un túnel o proxy (Cloudflare, Render…)
  HTTPS=1          si se accede por https (cookie de sesión solo cifrada)
"""
import os

from waitress import serve

from app import app

if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8080"))
    print(f"Delivery Hub en http://{host}:{port}/  (frontend en /app/). Ctrl+C para parar.")
    serve(app, host=host, port=port, threads=8)
