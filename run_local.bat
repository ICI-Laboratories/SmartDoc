@echo off
setlocal
cd /d "%~dp0"
echo SmartDoc - AIlauncher gateway
:: Apps use a preconfigured gateway; this launcher does not manage models.
if not defined LLM_GATEWAY_BASE_URL (
    echo Set LLM_GATEWAY_BASE_URL to the gateway URL including /v1.
    exit /b 1
)
if not defined LLM_GATEWAY_API_KEY (
    echo Set LLM_GATEWAY_API_KEY to the application gateway credential.
    exit /b 1
)
if not defined SMARTREVIEW_MODEL set "SMARTREVIEW_MODEL=sara-main"
:: Optional SARA_EMBEDDING_MODEL and SARA_OCR_MODEL stay empty in phase 1.

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
echo [1/2] Starting services...
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
echo [2/2] All services starting!
echo ========================================
echo.
echo Services:
echo   - API Gateway:        http://localhost:8043
echo   - LLM Service:        http://localhost:8044
echo   - Document Processor: http://localhost:8045
echo   - Frontend:           http://127.0.0.1:8501
echo.
echo Inference: AIlauncher gateway
echo.
echo Press any key to open the frontend in browser...
pause >nul

start http://127.0.0.1:8501

echo.
echo Batch ingestion is disabled until auth_services provides workload identity.
echo See docs\AUTH_CUTOVER.md.
echo.
echo To stop all services, close the terminal windows or press Ctrl+C in each.
echo.
