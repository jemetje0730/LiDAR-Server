@echo off
cd /d %~dp0
echo 🔄 백엔드 서버 실행 중...
start cmd /k "python backend.py"
