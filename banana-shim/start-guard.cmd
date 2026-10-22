@echo off
REM ASCII only + CRLF. Start banana-shim under its guard.
REM The guard restarts the shim if it exits (crash / closed browser / OOM).
setlocal
set HERE=%~dp0
set PY=%HERE%..\.venv\Scripts\python.exe
if not exist "%PY%" set PY=python

echo [guard] starting banana-shim under supervision
echo [guard] log: %HERE%logs\guard-%%DATE:~0,4%%.log
echo [guard] Ctrl+C to stop
echo.
"%PY%" "%HERE%guard-shim.py"
echo.
echo [guard] exited with code %ERRORLEVEL%
pause
