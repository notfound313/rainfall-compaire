@echo off
"%~dp0..\.venv\Scripts\python.exe" -m uvicorn app.main:app --app-dir "%~dp0." --host 127.0.0.1 --port 8000
pause
