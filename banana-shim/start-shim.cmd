@echo off
REM ASCII only + CRLF. Launcher for banana-shim.
REM Config lives in shim.env next to this file; shim.py reads it.
setlocal
set HERE=%~dp0
set PY=%HERE%..\.venv\Scripts\python.exe
if not exist "%PY%" set PY=python

echo [banana-shim] starting from %HERE%
echo [banana-shim] config: shim.env
echo.
"%PY%" "%HERE%shim.py" --host 127.0.0.1 --port 8790
echo.
echo [banana-shim] exited with code %ERRORLEVEL%
pause
