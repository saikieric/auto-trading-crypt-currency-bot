@echo off
echo ========================================
echo  Trading Bot Setup
echo ========================================

py --version > nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found.
    echo Please install Python 3.11+ from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('py --version 2^>^&1') do set PYVER=%%v
echo [OK] Python %PYVER% found

if not exist ".venv" (
    echo [INFO] Creating virtual environment...
    py -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo [OK] Virtual environment created
) else (
    echo [OK] Virtual environment already exists
)

echo [INFO] Installing dependencies...
.venv\Scripts\python.exe -m pip install --upgrade pip -q
.venv\Scripts\pip.exe install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)
echo [OK] Dependencies installed

if not exist "config.yaml" (
    copy config.example.yaml config.yaml > nul
    echo [OK] config.yaml created
) else (
    echo [OK] config.yaml already exists
)

if not exist ".env" (
    copy .env.example .env > nul
    echo [OK] .env created - please fill in your API keys
) else (
    echo [OK] .env already exists
)

if not exist "storage" mkdir storage
if not exist "logs" mkdir logs

echo.
echo ========================================
echo  Setup complete!
echo ========================================
echo.
echo Next steps:
echo   1. Edit .env with your API keys
echo   2. Edit config.yaml if needed
echo   3. Run start.bat to launch the bot
echo.
pause
