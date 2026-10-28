@echo off
REM Arranca Delivery Hub en este equipo y abre el navegador en http://127.0.0.1:8080/app/
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Primera ejecucion: creando entorno virtual e instalando dependencias...
  python -m venv .venv || goto :error
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :error
)

start "Delivery Hub (no cerrar)" cmd /k ".venv\Scripts\python.exe servidor.py"
timeout /t 3 /nobreak >nul
start "" http://127.0.0.1:8080/app/
exit /b 0

:error
echo Ha ocurrido un error. Comprueba que Python 3 esta instalado y en el PATH.
pause
exit /b 1
