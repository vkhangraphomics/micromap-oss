FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Install the in-repo MapForge package so api/ can reuse submit_bundle() (#206).
# Must come AFTER `COPY . .` — the package source isn't present at requirements time.
RUN pip install --no-cache-dir ./micromap-mapforge

# Create non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Expose port
EXPOSE 8200

# Run the API
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8200"]
