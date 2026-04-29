@echo off
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found. Please run setup.bat first.
    pause
    exit /b 1
)

if not exist "config.yaml" (
    echo [ERROR] config.yaml not found. Please run setup.bat first.
    pause
    exit /b 1
)

echo Starting bot... (Press Ctrl+C to stop)
.venv\Scripts\python.exe main.py config.yaml
