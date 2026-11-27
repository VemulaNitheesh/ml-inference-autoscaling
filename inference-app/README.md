# ML Inference API

This FastAPI service loads the existing serialized RandomForest model exactly once during application startup. It does not retrain or modify the model.

## Run locally

From the repository root:

```bash
.venv/bin/uvicorn app:app --app-dir inference-app --host 127.0.0.1 --port 8000
```

Then open interactive API documentation at `http://127.0.0.1:8000/docs`.

## Endpoints

- `GET /health` returns `healthy` only when the model was loaded during startup.
- `POST /predict` requires a `features` object containing exactly the 30 feature names from `ml-model/models/model_metadata.json`. Values must be finite numbers.

A prediction response contains the mapped class label and class probabilities in the model's class order. Validation errors use FastAPI's standard HTTP 422 response. A model unavailable at startup results in HTTP 503; unexpected prediction failures return HTTP 500 without an internal stack trace.

## Generate a valid local test payload

```bash
PYTHONPATH=inference-app .venv/bin/python - <<'PY'
from fastapi.testclient import TestClient
from app import app
from sklearn.datasets import load_breast_cancer

sample = load_breast_cancer().data[0]
with TestClient(app) as client:
    names = app.state.model_bundle.feature_names
    response = client.post('/predict', json={'features': dict(zip(names, sample.tolist()))})
    print(response.status_code, response.json())
PY
```

## Optional controlled CPU workload

The default is disabled: `CPU_WORKLOAD_ITERATIONS=0`. For characterization only, set a positive bounded iteration count and matrix size before starting Uvicorn:

```bash
CPU_WORKLOAD_ITERATIONS=10 CPU_WORKLOAD_MATRIX_SIZE=128 \
  .venv/bin/uvicorn app:app --app-dir inference-app --host 127.0.0.1 --port 8000
```

The workload builds a fixed matrix/vector once at startup and, per prediction, performs the configured number of deterministic matrix-vector multiplications followed by `tanh`. It does not alter the input sent to the classifier, the classifier result, or the response format. It has no sleep calls and is bounded to 0--100 iterations and 16--512 matrix dimension. The CPU-bound prediction endpoint is synchronous so FastAPI runs it in a worker thread, leaving the event loop available for the health endpoint.
