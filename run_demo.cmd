@echo off
setlocal EnableExtensions DisableDelayedExpansion
call "%~dp0run_preview.cmd" --demo
exit /b %errorlevel%
