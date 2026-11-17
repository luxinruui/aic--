@echo off
setlocal EnableExtensions DisableDelayedExpansion
call "%~dp0run_pc.cmd" --dual-robot %*
exit /b %ERRORLEVEL%
