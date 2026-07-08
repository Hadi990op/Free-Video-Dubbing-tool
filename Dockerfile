FROM python:3.12-slim

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies (torch CPU pinned <2.9 for CPU-only compat)
COPY requirements.txt .
RUN pip install --no-cache-dir "torch>=2.0,<2.9" "torchaudio>=2.0,<2.9" --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY dubber.py web_ui.py cleanup_jobs.py ./
COPY templates/ ./templates/ 2>/dev/null || true

# Create runtime directories
RUN mkdir -p uploads outputs

# Required env vars for voice cloning
ENV COQUI_TOS_AGREED=1
ENV HF_HUB_DISABLE_TELEMETRY=1

EXPOSE 5050

CMD ["gunicorn", "web_ui:app", \
     "--bind", "0.0.0.0:5050", \
     "--workers", "1", \
     "--threads", "8", \
     "--timeout", "0", \
     "--worker-class", "gthread"]
