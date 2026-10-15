@echo off
rem Demo de OpenNCCM en Windows: equipo Cisco simulado + aplicacion web en http://127.0.0.1:8000
set "PY=%TEMP%\ncmpy\python.exe"
if not exist "%PY%" (
  echo No se encontro el Python portable en %TEMP%\ncmpy.
  echo Pidale a Claude que lo vuelva a preparar.
  pause
  exit /b 1
)
set "NCM_DATA_DIR=%TEMP%\ncmtest"
set "NCM_KEY_FILE=%TEMP%\ncmtest\secret.key"
set "NCM_SYSLOG_PORT=5514"

echo Iniciando el equipo Cisco simulado (127.0.0.1:2222)...
start "OpenNCCM - equipo simulado" /min "%PY%" "%~dp0..\..\tests\fakedev.py" 2222

echo Iniciando OpenNCCM en http://127.0.0.1:8000  (cierre esta ventana para detenerlo)
cd /d "%~dp0..\.."
start "" cmd /c "timeout /t 4 >nul & start http://127.0.0.1:8000"
"%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
