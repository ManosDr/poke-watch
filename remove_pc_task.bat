@echo off
rem Stops the hourly poke watch check on this PC. GitHub keeps checking the other shops.
schtasks /Delete /F /TN "Poke watch"
echo.
pause
