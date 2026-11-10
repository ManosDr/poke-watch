@echo off
setlocal
cd /d "%~dp0"
title Poke watch uninstall
echo This removes poke watch from this PC:
echo   - the scheduled task "Poke watch"
echo   - the private Python environment (.venv) and the downloaded browser (browsers)
echo   - the debug folder, if there is one
echo Your settings (poke_watch.env), state.json and poke_watch.log are asked about separately.
echo Nothing outside this folder is touched, except the scheduled task.
echo.
choice /C YN /M "Continue"
if errorlevel 2 goto :cancel

schtasks /Delete /F /TN "Poke watch" >nul 2>&1
if errorlevel 1 (echo No scheduled task called "Poke watch" was found, so nothing to stop.) else (echo Scheduled task removed.)

if exist ".venv" (rmdir /s /q ".venv" & echo Removed .venv)
if exist "browsers" (rmdir /s /q "browsers" & echo Removed browsers)
if exist "debug" (rmdir /s /q "debug" & echo Removed debug)
if exist "__pycache__" (rmdir /s /q "__pycache__")

echo.
choice /C YN /M "Also delete your settings (poke_watch.env), state.json and poke_watch.log"
if not errorlevel 2 (
    del /q "poke_watch.env" "state.json" "poke_watch.log" 2>nul
    echo Deleted the settings, state and log.
)

echo.
echo Done. To finish, close this window and delete this whole folder:
echo   %~dp0
echo If you installed an older version of poke watch, its browser may also be in
echo   %%LOCALAPPDATA%%\ms-playwright   (safe to delete if you use no other Playwright tools)
echo.
pause
exit /b 0

:cancel
echo Nothing was changed.
pause
exit /b 1
