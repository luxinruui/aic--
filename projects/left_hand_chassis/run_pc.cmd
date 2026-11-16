@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if not defined LEFT_HAND_PYTHON if exist ".local-python.txt" set /p "LEFT_HAND_PYTHON="<".local-python.txt"
echo AIC LEFT-HAND CHASSIS - starting. Please keep this window open.
if not exist "logs\startup" mkdir "logs\startup"
echo AIC Windows launcher started.>"logs\startup\launcher.log"
if not exist "scripts\bootstrap.py" (
  echo Project files are incomplete. Extract ALL files from the ZIP first.
  echo Missing scripts\bootstrap.py>>"logs\startup\launcher.log"
  set "AIC_EXIT=2"
  goto failed
)
if defined LEFT_HAND_PYTHON (
  set "AIC_PYTHON=%LEFT_HAND_PYTHON%"
  goto use_python
)
where py >nul 2>nul
if not errorlevel 1 goto use_py
if exist ".venv\Scripts\python.exe" (
  set "AIC_PYTHON=.venv\Scripts\python.exe"
  goto use_python
)
if exist "%USERPROFILE%\miniconda3\envs\robot_vision\python.exe" (
  set "AIC_PYTHON=%USERPROFILE%\miniconda3\envs\robot_vision\python.exe"
  goto use_python
)
if exist "%USERPROFILE%\anaconda3\envs\robot_vision\python.exe" (
  set "AIC_PYTHON=%USERPROFILE%\anaconda3\envs\robot_vision\python.exe"
  goto use_python
)
set "AIC_PYTHON=python"
goto use_python
:use_py
set "AIC_PY_VERSION=-3"
for %%V in (3.10 3.11 3.12) do (
  py -%%V --version >nul 2>nul
  if not errorlevel 1 (
    set "AIC_PY_VERSION=-%%V"
    goto start_py
  )
)
:start_py
py %AIC_PY_VERSION% -u scripts\bootstrap.py %* 2>>"logs\startup\launcher.log"
goto finished
:use_python
"%AIC_PYTHON%" -u scripts\bootstrap.py %* 2>>"logs\startup\launcher.log"
:finished
set "AIC_EXIT=%ERRORLEVEL%"
>>"logs\startup\launcher.log" echo Launcher exit code: %AIC_EXIT%
if "%AIC_EXIT%"=="0" exit /b 0
:failed
echo.
echo STARTUP FAILED. Check logs\startup\launcher.log and latest.log if present.
type "logs\startup\launcher.log"
echo Please send the log file or a screenshot of this window.
if not "%AIC_NO_PAUSE%"=="1" pause
exit /b %AIC_EXIT%
