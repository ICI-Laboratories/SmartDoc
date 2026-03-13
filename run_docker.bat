@echo off
setlocal EnableDelayedExpansion

:: SmartReview Docker Runner for Ollama
:: This script starts Ollama locally and then runs all other services via Docker Compose

echo ========================================
echo  SmartReview - Docker Setup (Local Ollama)
echo ========================================
echo.

:: Configuration - use the fastest model by default
set "MODEL=qwen3-vl:latest"
set "CONTEXT_SIZE=8192"

:: Check if user wants granite4 or gemma instead
if "%1"=="granite" (
    set "MODEL=granite4:7b-a1b-h"
    set "CONTEXT_SIZE=16384"
    echo Using granite4 model (4.2GB - more context headroom but slower)
) else if "%1"=="gemma" (
    set "MODEL=gemma3n:e2b-it-fp16"
    set "CONTEXT_SIZE=8192"
    echo Using gemma3n model (8.9GB - higher reasoning)
) else (
    echo Using qwen3-vl:latest model (Fastest for batch ingestion)
)
echo Model: %MODEL%
echo Context: %CONTEXT_SIZE% tokens
echo.

:: Copy local environment config
echo [1/4] Setting up environment...
copy /Y ".env.local" ".env" >nul 2>&1
if errorlevel 1 (
    echo Warning: Could not copy .env.local - using existing .env
)

:: Update model in .env if needed
powershell -Command "(Get-Content .env) -replace 'SMARTREVIEW_MODEL=.*', 'SMARTREVIEW_MODEL=%MODEL%' | Set-Content .env"

:: Check if Ollama is installed
where ollama >nul 2>&1
if errorlevel 1 (
    echo ERROR: Ollama not found in PATH
    echo Please install Ollama from https://ollama.ai
    pause
    exit /b 1
)

:: Check if model is available
echo [2/4] Checking Ollama model...
ollama list | findstr /C:"%MODEL%" >nul 2>&1
if errorlevel 1 (
    echo Model %MODEL% not found. Pulling it now...
    ollama pull %MODEL%
    if errorlevel 1 (
        echo ERROR: Failed to pull model %MODEL%
        pause
        exit /b 1
    )
)

:: Preload model with context settings
echo [3/4] Preloading model into VRAM (this may take a moment)...
:: Start Ollama serve if not running
tasklist /FI "IMAGENAME eq ollama.exe" 2>NUL | find /I /N "ollama.exe">NUL
if errorlevel 1 (
    echo Starting Ollama server...
    start /B ollama serve >nul 2>&1
    timeout /t 3 /nobreak >nul
)

:: Warm up the model with context size
curl -s -X POST http://localhost:11434/api/generate -d "{\"model\":\"%MODEL%\",\"prompt\":\"hi\",\"options\":{\"num_ctx\":%CONTEXT_SIZE%},\"stream\":false}" >nul 2>&1
echo Model loaded with %CONTEXT_SIZE% context window

:: Get the script directory
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

echo.
echo [4/4] Starting Docker services...
echo.

:: Check if Docker is running
docker info >nul 2>&1
if errorlevel 1 (
    echo ERROR: Docker is not running or not installed.
    echo Please start Docker Desktop and try again.
    pause
    exit /b 1
)

:: Run docker-compose up
echo Building and starting containers (this might take a while the first time)...
docker-compose up -d --build

echo.
echo [OK] All services starting via Docker!
echo ========================================
echo.
echo Services:
echo   - API Gateway:        http://localhost:8080
echo   - Frontend:           http://localhost:8501
echo.
echo Ollama is running natively on your machine!
echo.
echo Press any key to open the frontend in browser...
pause >nul

start http://localhost:8501

echo.
echo To process the massive PDF dataset, please open a NEW TERMINAL and run:
echo    cd "C:\proyectosicilabs\SmartDoc"
echo    call env\Scripts\activate.bat
echo    python batch_ingest.py
echo.
echo (Or point batch_ingest.py to http://127.0.0.1:8002/process_document/)
echo.
echo To stop the Docker containers, run: docker-compose down
echo.
