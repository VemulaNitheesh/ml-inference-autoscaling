"""FastAPI application for the saved Breast Cancer classifier."""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

import numpy as np
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest
from starlette.concurrency import run_in_threadpool

from cpu_workload import CpuWorkload, WorkloadConfigurationError
from model_loader import ModelBundle, load_model_bundle
from schemas import HealthResponse, PredictionRequest, PredictionResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
LOGGER = logging.getLogger("ml_inference")

# Counts requests accepted but not yet completed, including worker-thread wait time.
IN_FLIGHT = Gauge(
    "inference_requests_in_flight",
    "Prediction requests accepted and not yet completed",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load inference resources once during application startup."""
    LOGGER.info("Starting inference application")
    app.state.model_bundle = None
    app.state.cpu_workload = None
    app.state.model_load_error = None

    try:
        app.state.model_bundle = load_model_bundle()
        app.state.cpu_workload = CpuWorkload.from_environment()

        LOGGER.info("Model loaded successfully: %s", app.state.model_bundle.model_name)
        LOGGER.info(
            "Controlled CPU workload: enabled=%s iterations=%s matrix_size=%s",
            app.state.cpu_workload.enabled,
            app.state.cpu_workload.iterations,
            app.state.cpu_workload.matrix_size,
        )
    except (RuntimeError, WorkloadConfigurationError):
        app.state.model_load_error = "Model is unavailable"
        LOGGER.exception("Application startup failed")

    yield
    LOGGER.info("Stopping inference application")


app = FastAPI(
    title="ML Inference API",
    description="REST inference API for the autoscaling evaluation project",
    version="1.0.0",
    lifespan=lifespan,
)


def resources_or_503(request: Request) -> tuple[ModelBundle, CpuWorkload]:
    bundle = getattr(request.app.state, "model_bundle", None)
    workload = getattr(request.app.state, "cpu_workload", None)

    if bundle is None or workload is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model is unavailable",
        )

    return bundle, workload


@app.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """Report application resource availability."""
    if getattr(request.app.state, "model_bundle", None) is None:
        return HealthResponse(status="unhealthy", model=None)

    return HealthResponse(
        status="healthy",
        model=request.app.state.model_bundle.model_name,
    )


def _predict_sync(payload: PredictionRequest, request: Request) -> PredictionResponse:
    """Execute CPU-bound prediction in a worker thread."""
    bundle, workload = resources_or_503(request)

    started = time.perf_counter()
    LOGGER.info("Prediction request started")

    try:
        ordered_values = [
            payload.features[name] for name in bundle.feature_names
        ]

        feature_vector = np.asarray(
            ordered_values, dtype=float
        ).reshape(1, -1)

        predicted_class_index = int(bundle.model.predict(feature_vector)[0])
        probabilities = bundle.model.predict_proba(feature_vector)[0]

        workload.run()

        if not 0 <= predicted_class_index < len(bundle.target_class_names):
            raise RuntimeError("Model returned an unknown class index")

        if len(probabilities) != len(bundle.target_class_names):
            raise RuntimeError("Model returned an invalid probability vector")

        response = PredictionResponse(
            prediction=bundle.target_class_names[predicted_class_index],
            probabilities={
                name: float(value)
                for name, value in zip(
                    bundle.target_class_names, probabilities
                )
            },
        )

    except HTTPException:
        raise
    except Exception:
        LOGGER.exception("Prediction request failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Prediction could not be completed",
        ) from None

    elapsed_ms = (time.perf_counter() - started) * 1000

    LOGGER.info(
        "Prediction request completed outcome=success duration_ms=%.2f",
        elapsed_ms,
    )

    return response


@app.post("/predict", response_model=PredictionResponse)
async def predict(
    payload: PredictionRequest,
    request: Request,
) -> PredictionResponse:
    """Count the request before submitting prediction to the worker pool."""
    IN_FLIGHT.inc()

    try:
        return await run_in_threadpool(
            _predict_sync,
            payload,
            request,
        )
    finally:
        IN_FLIGHT.dec()


@app.get("/metrics")
def metrics() -> Response:
    """Expose application metrics in Prometheus format."""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
