@echo off
if "%~1"=="" (echo Usage: RESTORE_WINDOWS.bat path-to-backup.sqlite3 & pause & exit /b 1)
set /p CONFIRM=Stop the app first. Restore this backup? Type YES: 
if /I not "%CONFIRM%"=="YES" exit /b 1
python restore_db.py "%~1" --yes
pause
