@echo off
rem Start Orange Live Translate (overlay window). Extra args are passed through, e.g. run.bat --no-overlay
cd /d "%~dp0"
set PYTHONPATH=%~dp0src
".venv\Scripts\python.exe" -m live_translator %*
