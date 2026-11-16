@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" scripts\sync_github.py
) else (
  where py >nul 2>nul
  if errorlevel 1 (
    python scripts\sync_github.py
  ) else (
    py -3 scripts\sync_github.py
  )
)
if errorlevel 1 (
  echo Sync failed. Logs are still saved on this computer.
  pause
  exit /b 1
)
pause
