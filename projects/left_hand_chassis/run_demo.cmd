@echo off
setlocal EnableExtensions DisableDelayedExpansion
call "%~dp0run_pc.cmd" --demo %*
exit /b %ERRORLEVEL%
