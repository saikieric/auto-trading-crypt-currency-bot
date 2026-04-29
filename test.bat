@echo off
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found. Please run setup.bat first.
    pause
    exit /b 1
)

.venv\Scripts\pip.exe install -r requirements-dev.txt -q
echo Running tests...
.venv\Scripts\python.exe -m pytest tests/ -v
pause
