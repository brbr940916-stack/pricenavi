@echo off
chcp 65001 >nul
python backup_db.py
if errorlevel 1 echo 백업 실패: 위 오류를 확인하세요.
pause
