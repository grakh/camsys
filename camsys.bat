@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PY_CMD=
where pyw >nul 2>nul && set PY_CMD=pyw
if "%PY_CMD%"=="" (where pythonw >nul 2>nul && set PY_CMD=pythonw)
if "%PY_CMD%"=="" (where py >nul 2>nul && set PY_CMD=py)
if "%PY_CMD%"=="" (where python >nul 2>nul && set PY_CMD=python)
if "%PY_CMD%"=="" (
    echo [ERROR] Python not found. Install from https://www.python.org/downloads/
    pause & exit /b 1
)
set PY_CHECK=py
where py >nul 2>nul || set PY_CHECK=python
%PY_CHECK% -c "import PySide6" >nul 2>nul
if errorlevel 1 (
    echo [WARNING] PySide6 not installed. Run install.bat first.
    pause & exit /b 1
)
start "" %PY_CMD% main.py gui
