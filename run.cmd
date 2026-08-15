@echo off
title VNStock Chart - Local Server

echo ========================================
echo    VNStock Chart - Local Server
echo ========================================
echo.

if not exist "venv\" (
    echo [*] Chua co moi truong ao, dang tao...
    python -m venv venv
    echo [OK] Da tao venv
)

call venv\Scripts\activate.bat

echo [*] Dang cai thu vien...
pip install -r requirements.txt -q

echo.
echo [OK] Server chay tai: http://localhost:8890
echo [*] Nhan Ctrl+C de dung
echo.

start /b cmd /c "timeout /t 2 >nul && start http://localhost:8890"

uvicorn main:app --host 127.0.0.1 --port 8890 --reload

pause
