@echo off
cd /d %~dp0
call .\venv\Scripts\activate.bat
python main.py
if errorlevel 1 (
    echo Error occurred during execution. Error code: %errorlevel%
    exit /b %errorlevel%
)
exit /b 0