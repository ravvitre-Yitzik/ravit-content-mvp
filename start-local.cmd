@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Missing .venv. Follow the setup instructions in README.md first.
  pause
  exit /b 1
)
echo Open http://127.0.0.1:8765 in your browser.
echo Keep this window open. Press Ctrl+C to stop the local application.
echo Generation requires explicit consent in the application.
".venv\Scripts\python.exe" -B -u "local_ui.py" --allow-work
if errorlevel 1 pause
endlocal
