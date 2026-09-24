FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy service code
COPY llm_service ./llm_service
COPY library_service ./library_service

# Expose port
EXPOSE 8001

CMD ["uvicorn", "llm_service.api:app", "--host", "0.0.0.0", "--port", "8001"]
