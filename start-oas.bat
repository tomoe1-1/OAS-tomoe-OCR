@echo off
cd /d "%~dp0"
"%~dp0toolkit\python.exe" -X utf8 -B verify_deployment.py
if errorlevel 1 goto failed
echo OAS service address: http://127.0.0.1:22388
"%~dp0toolkit\python.exe" -B server.py --host 127.0.0.1 --port 22388
pause
exit /b
:failed
echo Package verification failed. Extract a fresh package into a new directory.
pause
exit /b 1
