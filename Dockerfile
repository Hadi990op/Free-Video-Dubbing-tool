FROM python:3.12-slim

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies (torch CPU first for smaller image)
COPY requirements.txt .
RUN pip install --no-cache-dir torch torchaudio --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY dubber.py web_ui.py cleanup_jobs.py ./
COPY templates/ ./templates/ 2>/dev/null || true

# Create runtime directories
RUN mkdir -p uploads outputs

EXPOSE 5050

CMD ["gunicorn", "web_ui:app", \
     "--bind", "0.0.0.0:5050", \
     "--workers", "1", \
     "--threads", "8", \
     "--timeout", "0", \
     "--worker-class", "gthread"]
