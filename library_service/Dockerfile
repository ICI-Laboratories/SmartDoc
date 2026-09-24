FROM python:3.12-slim-bookworm
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-spa tesseract-ocr-eng && rm -rf /var/lib/apt/lists/*
COPY library_service/requirements.lock /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY library_service /app/library_service
RUN useradd --uid 10001 --create-home sara && mkdir /data && chown sara:sara /data
USER sara
ENV SARA_STORAGE=/data PYTHONUNBUFFERED=1
CMD ["uvicorn", "library_service.api:app", "--host", "0.0.0.0", "--port", "8003", "--no-access-log"]
