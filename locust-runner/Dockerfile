FROM python:3.12-slim
WORKDIR /loadtest
RUN pip install --no-cache-dir locust==2.47.0
COPY locustfile.py .
COPY payload.json .
EXPOSE 8089
CMD ["locust", "-f", "locustfile.py", "--host", "http://ml-inference.default.svc.cluster.local:8000", "--web-host", "0.0.0.0"]
