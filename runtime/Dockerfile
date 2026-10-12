FROM python:3.14-slim
RUN apt-get update && apt-get install -y --no-install-recommends git nodejs npm poppler-utils tesseract-ocr tesseract-ocr-fra && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir pytest
WORKDIR /workspace
USER 65534
