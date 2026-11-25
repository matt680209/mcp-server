@echo off
setlocal
cd /d "%~dp0"
title Avatar MCP

if /i "%~1"=="stop" goto stop
if /i "%~1"=="set-password" goto password
if not "%~1"=="" (
    echo Usage: local_start.bat [stop^|set-password]
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Python virtual environment not found.
    echo Create .venv and install requirements.txt as described in README.md.
    pause
    exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0local_start.ps1"
set "EXITCODE=%ERRORLEVEL%"
exit /b %EXITCODE%

:stop
if not "%~2"=="" (
    echo Usage: local_start.bat [stop^|set-password]
    exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0local_start.ps1" -Action stop
exit /b %ERRORLEVEL%

:password
if not "%~2"=="" (
    echo Usage: local_start.bat [stop^|set-password]
    exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0local_start.ps1" -Action set-password
exit /b %ERRORLEVEL%
