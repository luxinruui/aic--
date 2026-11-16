@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
echo AIC diagnostic mode. This window stays open after the preview exits.
set "AIC_NO_PAUSE=1"
cmd /d /k call "%~dp0run_preview.cmd"
