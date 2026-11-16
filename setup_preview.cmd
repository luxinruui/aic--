@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" scripts\setup_preview.py
) else (
  where py >nul 2>nul
  if errorlevel 1 (
    python scripts\setup_preview.py
  ) else (
    py -3 scripts\setup_preview.py
  )
)
if errorlevel 1 (
  echo Setup failed. See the message above.
  pause
  exit /b 1
)
echo Ready. Double-click run_preview.cmd to start.
pause
