@echo off
REM Publica Delivery Hub en internet con un túnel de Cloudflare.
REM El enlace público (https://....trycloudflare.com) aparece en la ventana "Túnel". Cambia cada vez que se arranca.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv || goto :error
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :error
)

set CF="C:\Program Files (x86)\cloudflared\cloudflared.exe"
if not exist %CF% set CF="C:\Program Files\cloudflared\cloudflared.exe"
if not exist %CF% (
  echo Instalando cloudflared...
  winget install --id Cloudflare.cloudflared -e --accept-source-agreements --accept-package-agreements || goto :error
)

start "Delivery Hub - Servidor (no cerrar)" cmd /k "set TRUST_PROXY=1&& set HTTPS=1&& .venv\Scripts\python.exe servidor.py"
timeout /t 3 /nobreak >nul
start "Delivery Hub - Tunel (no cerrar) - busca aqui el enlace https" cmd /k %CF% tunnel --no-autoupdate --url http://127.0.0.1:8080
echo.
echo Busca en la ventana del Tunel la linea con https://....trycloudflare.com
echo Abre ese enlace y anade /app/ al final.
pause
exit /b 0

:error
echo Ha ocurrido un error.
pause
exit /b 1
