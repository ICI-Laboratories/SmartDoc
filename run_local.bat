@echo off
setlocal EnableDelayedExpansion

:: SmartReview Local Runner for Ollama (RTX 3090)
:: This script starts all services using local Ollama models

echo ========================================
echo  SmartReview - Local Ollama Setup
echo  Optimized for RTX 3090 (24GB VRAM)
echo ========================================
echo.

:: Configuration - use the fastest model by default for RTX 4060
set "MODEL=qwen3-vl:latest"
set "CONTEXT_SIZE=8192"

:: Check if user wants granite4 instead (smaller but slower first token)
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
echo [1/5] Setting up environment...
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
echo [2/5] Checking Ollama model...
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
echo [3/5] Preloading model into VRAM (this may take a moment)...
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

:: Activate virtual environment if exists
if exist "env\Scripts\activate.bat" (
    call env\Scripts\activate.bat
    echo Virtual environment activated
) else (
    echo Warning: No virtual environment found at env\
    echo Make sure dependencies are installed: pip install -r requirements.txt
)

echo.
echo [4/5] Starting services...
echo.

:: Start LLM Service (must start before gateway)
echo Starting LLM Service (port 8044)...
start "SmartReview - LLM Service" cmd /k "cd /d %SCRIPT_DIR% && call env\Scripts\activate.bat 2>nul && uvicorn llm_service.api:app --host 127.0.0.1 --port 8044"

:: Start Document Processor
echo Starting Document Processor (port 8045)...
start "SmartReview - Document Processor" cmd /k "cd /d %SCRIPT_DIR% && call env\Scripts\activate.bat 2>nul && uvicorn document_processor.app:app --host 127.0.0.1 --port 8045"

:: Wait for backend services to start
timeout /t 3 /nobreak >nul

:: Start API Gateway (Rust) - listens on port 8043
echo Starting API Gateway (Rust - port 8043)...
start "SmartReview - API Gateway" cmd /k "cd /d %SCRIPT_DIR%api_gateway && cargo run"

:: Wait for gateway to start
timeout /t 2 /nobreak >nul

:: Start Frontend
echo Starting Frontend (Streamlit)...
start "SmartReview - Frontend" cmd /k "cd /d %SCRIPT_DIR%frontend && call ..\env\Scripts\activate.bat 2>nul && streamlit run app.py"

echo.
echo [5/5] All services starting!
echo ========================================
echo.
echo Services:
echo   - API Gateway:        http://localhost:8043
echo   - LLM Service:        http://localhost:8044
echo   - Document Processor: http://localhost:8045
echo   - Frontend:           http://localhost:8501
echo.
echo Ollama:
echo   - Model: %MODEL%
echo   - Context: %CONTEXT_SIZE% tokens
echo   - Endpoint: http://localhost:11434
echo.
echo Press any key to open the frontend in browser...
pause >nul

start http://localhost:8501

echo.
echo To process the massive 14,000 PDF dataset, please open a NEW TERMINAL and run:
echo    cd "C:\proyectosicilabs\SmartDoc"
echo    call env\Scripts\activate.bat
echo    python batch_ingest.py
echo.
echo To stop all services, close the terminal windows or press Ctrl+C in each.
echo.
