@echo off
setlocal EnableExtensions DisableDelayedExpansion
call "%~dp0run_pc.cmd" --robot %*
exit /b %ERRORLEVEL%
