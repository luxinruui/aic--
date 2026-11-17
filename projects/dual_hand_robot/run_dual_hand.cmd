@echo off
setlocal EnableExtensions DisableDelayedExpansion
call "%~dp0run_pc.cmd" --upper %*
exit /b %ERRORLEVEL%
