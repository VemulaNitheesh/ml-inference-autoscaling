FROM python:3.12-slim

WORKDIR /app

# Install application dependencies
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# Copy application and trained model
COPY app/ ./app/
COPY models/ ./models/

# Application configuration
ENV PYTHONUNBUFFERED=1
ENV MAX_CONCURRENT_INFERENCES=2

# Expose FastAPI port
EXPOSE 8000

# Start one Uvicorn worker per container
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

