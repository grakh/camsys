@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PY_CMD=
where py >nul 2>nul && set PY_CMD=py
if "%PY_CMD%"=="" (where python >nul 2>nul && set PY_CMD=python)
if "%PY_CMD%"=="" (echo [ERROR] Python not found. & pause & exit /b 1)
%PY_CMD% --version
%PY_CMD% -m pip install --upgrade pip
%PY_CMD% -m pip install -r requirements.txt
pause
