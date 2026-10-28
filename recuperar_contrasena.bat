@echo off
REM Pone una contraseña temporal al usuario admin (o al que se indique: recuperar_contrasena.bat gerente)
cd /d "%~dp0"
set USUARIO=%1
if "%USUARIO%"=="" set USUARIO=admin
".venv\Scripts\flask.exe" --app app reset-password %USUARIO%
echo.
pause
