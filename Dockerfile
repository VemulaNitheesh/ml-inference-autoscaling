FROM python:3.11-slim

WORKDIR /app

COPY inference-app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY inference-app/ ./inference-app/
COPY ml-model/ ./ml-model/

EXPOSE 8000

CMD ["uvicorn", "app:app", "--app-dir", "/app/inference-app", "--host", "0.0.0.0", "--port", "8000"]
