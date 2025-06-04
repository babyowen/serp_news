@echo off
chcp 65001
REM 切换到脚本所在目录
cd /d %~dp0

REM 检测是否在PowerShell下
set IS_PS=0
if not "%PSModulePath%"=="" set IS_PS=1

REM 激活虚拟环境
if %IS_PS%==1 (
    powershell -NoProfile -ExecutionPolicy Bypass -Command ".\venv\Scripts\Activate.ps1; python main.py; Read-Host -Prompt '按回车键退出'"
) else (
    call venv\Scripts\activate.bat
    python main.py
    pause
) 