@echo off
setlocal EnableExtensions DisableDelayedExpansion
call "%~dp0projects\left_hand_chassis\run_robot.cmd" %*
exit /b %ERRORLEVEL%
