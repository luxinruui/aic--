@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  call setup_preview.cmd
  if errorlevel 1 exit /b 1
)
".venv\Scripts\python.exe" -m aic_hand %*
if errorlevel 1 (
  echo Preview failed. See the message above or run setup_preview.cmd again.
  pause
  exit /b 1
)
