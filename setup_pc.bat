@echo off
setlocal
cd /d "%~dp0"
title Poke watch setup

rem Find Python 3.10 or newer. The "py" launcher comes with the python.org installer.
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PY=python"
if not defined PY goto :nopython

if not exist ".venv\Scripts\python.exe" (
    echo Creating a private Python environment for poke watch...
    %PY% -m venv .venv
)
if not exist ".venv\Scripts\python.exe" goto :novenv

".venv\Scripts\python.exe" setup_pc.py %*
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%

:nopython
echo.
echo Python 3.10 or newer isn't installed on this PC.
echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH",
echo or open a terminal and run:  winget install Python.Python.3.12
echo Then double-click setup_pc.bat again.
echo.
pause
exit /b 1

:novenv
echo.
echo Couldn't create the Python environment in "%~dp0.venv". See the messages above.
echo.
pause
exit /b 1
