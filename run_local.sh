#!/bin/bash
set -e

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}=== SmartDoc Local Runner ===${NC}"

# Function to kill background processes on exit
cleanup() {
    echo -e "\n${RED}Stopping all services...${NC}"
    kill $(jobs -p) 2>/dev/null
    wait
    echo -e "${GREEN}All services stopped.${NC}"
}
trap cleanup EXIT

# 1. Check for Rust
if ! command -v cargo &> /dev/null; then
    if [ -f "$HOME/.cargo/env" ]; then
        source "$HOME/.cargo/env"
    fi
fi

if ! command -v cargo &> /dev/null; then
    echo -e "${RED}Error: Rust (cargo) is not installed or not in PATH.${NC}"
    echo "Please install it via: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
    exit 1
fi

# 2. Install Python Dependencies
echo -e "${BLUE}Checking/Installing Python dependencies...${NC}"
pip install -r requirements.txt

# 3. Build API Gateway
echo -e "${BLUE}Building API Gateway (Rust)...${NC}"
cd api_gateway
cargo build --release
cd ..

# 4. Start Services
echo -e "${GREEN}Starting services...${NC}"

# Start API Gateway
echo "Starting API Gateway..."
cd api_gateway
cargo run --release &
GATEWAY_PID=$!
cd ..

# Start LLM Service
echo "Starting LLM Service..."
uvicorn llm_service.api:app --host 127.0.0.1 --port 8044 &
LLM_PID=$!

# Start Document Processor
echo "Starting Document Processor..."
uvicorn document_processor.app:app --host 127.0.0.1 --port 8045 &
DOCPROC_PID=$!

# Start Frontend
echo "Starting Frontend..."
cd frontend
streamlit run app.py &
FRONTEND_PID=$!
cd ..

echo -e "${GREEN}All services passed to background. Press Ctrl+C to stop.${NC}"

# Wait for all processes
wait
