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

echo.
echo [1/1] Starting Docker services...
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
docker compose up -d --build
if errorlevel 1 exit /b 1

echo.
echo [OK] All services starting via Docker!
echo ========================================
echo.
echo Services:
echo   - API Gateway:        http://localhost:8080
echo   - Frontend:           http://localhost:8501
echo.
echo Inference is provided by the configured AIlauncher gateway.
echo.
echo Press any key to open the frontend in browser...
pause >nul

start http://localhost:8501

echo.
echo Batch ingestion is disabled until auth_services provides workload identity.
echo See docs\AUTH_CUTOVER.md.
echo.
echo To stop the Docker containers, run: docker compose down
echo.
