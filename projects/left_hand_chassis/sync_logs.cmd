@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if defined LEFT_HAND_PYTHON (
  "%LEFT_HAND_PYTHON%" scripts\sync_logs.py
) else if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" scripts\sync_logs.py
) else if exist "%USERPROFILE%\anaconda3\envs\robot_vision\python.exe" (
  "%USERPROFILE%\anaconda3\envs\robot_vision\python.exe" scripts\sync_logs.py
) else (
  python scripts\sync_logs.py
)
set "SYNC_EXIT=%ERRORLEVEL%"
if not "%AIC_NO_PAUSE%"=="1" pause
exit /b %SYNC_EXIT%
