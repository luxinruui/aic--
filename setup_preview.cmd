@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
echo AIC setup only. Use run_preview.cmd to install and open the preview.
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
  if not "%AIC_NO_PAUSE%"=="1" pause
  exit /b 1
)
echo Ready. Double-click run_preview.cmd to start.
if not "%AIC_NO_PAUSE%"=="1" pause
