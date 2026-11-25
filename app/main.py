import asyncio
import json
import math
import os
import pickle
import time
from pathlib import Path

import numpy as np

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator

from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
)


# ==================================================
# 1. APPLICATION CONFIGURATION
# ==================================================

BASE_DIR = Path(__file__).resolve().parent.parent

MODEL_PATH = BASE_DIR / "models" / "model.pkl"
METADATA_PATH = BASE_DIR / "models" / "model_metadata.json"

MAX_CONCURRENT_INFERENCES = int(
    os.getenv("MAX_CONCURRENT_INFERENCES", "2")
)

if MAX_CONCURRENT_INFERENCES < 1:
    raise ValueError("MAX_CONCURRENT_INFERENCES must be at least 1")


# ==================================================
# 2. LOAD TRAINED MODEL
# ==================================================

with open(MODEL_PATH, "rb") as file:
    model = pickle.load(file)

with open(METADATA_PATH, "r") as file:
    metadata = json.load(file)

CLASS_NAMES = metadata["classes"]
FEATURE_COUNT = metadata["feature_count"]


# ==================================================
# 3. PROMETHEUS METRICS
# ==================================================

REQUESTS_TOTAL = Counter(
    "inference_requests_total",
    "Total number of valid inference requests",
)

REQUEST_ERRORS = Counter(
    "inference_errors_total",
    "Total number of failed inference requests",
)

REQUEST_DURATION = Histogram(
    "inference_request_duration_seconds",
    "Total inference request duration including queue waiting",
)

MODEL_EXECUTION_DURATION = Histogram(
    "inference_model_execution_seconds",
    "Time spent executing model predictions",
)

QUEUE_WAIT_DURATION = Histogram(
    "inference_queue_wait_duration_seconds",
    "Time spent waiting for inference capacity",
)

QUEUE_DEPTH = Gauge(
    "inference_queue_depth",
    "Current number of requests waiting for inference capacity",
)

PEAK_QUEUE_DEPTH = Gauge(
    "inference_peak_queue_depth",
    "Maximum observed waiting queue depth since application startup",
)

ACTIVE_REQUESTS = Gauge(
    "inference_active_requests",
    "Number of requests currently executing inference",
)


# ==================================================
# 4. CONCURRENCY AND QUEUE MANAGEMENT
# ==================================================

class InferenceSlots:

    def __init__(self, limit):

        self.limit = limit

        self.condition = asyncio.Condition()

        self.active = 0
        self.waiting = 0
        self.peak_waiting = 0

    async def acquire(self):

        start_time = time.perf_counter()

        async with self.condition:

            if self.active >= self.limit:

                # Request must wait for inference capacity
                self.waiting += 1

                QUEUE_DEPTH.set(self.waiting)

                self.peak_waiting = max(
                    self.peak_waiting,
                    self.waiting,
                )

                PEAK_QUEUE_DEPTH.set(self.peak_waiting)

                try:

                    await self.condition.wait_for(
                        lambda: self.active < self.limit
                    )

                finally:

                    # Remove request from waiting queue
                    self.waiting -= 1

                    QUEUE_DEPTH.set(self.waiting)

                    # Pass an available slot to another waiter
                    if self.active < self.limit:
                        self.condition.notify(1)

            # Request now owns an inference slot
            self.active += 1

            ACTIVE_REQUESTS.set(self.active)

        return time.perf_counter() - start_time

    async def release(self):

        async with self.condition:

            self.active -= 1

            ACTIVE_REQUESTS.set(self.active)

            # Notify a waiting request
            self.condition.notify(1)


inference_slots = InferenceSlots(
    MAX_CONCURRENT_INFERENCES
)


# ==================================================
# 5. FASTAPI APPLICATION
# ==================================================

app = FastAPI(
    title="ML Inference Service",
    description="Random Forest inference service for Kubernetes autoscaling research",
    version="1.0.0",
)


# ==================================================
# 6. REQUEST VALIDATION
# ==================================================

class PredictionRequest(BaseModel):

    features: list[float] = Field(
        min_length=FEATURE_COUNT,
        max_length=FEATURE_COUNT,
        description=f"Exactly {FEATURE_COUNT} numerical features",
    )

    @field_validator("features")
    @classmethod
    def validate_finite_features(cls, values):

        if not all(math.isfinite(value) for value in values):
            raise ValueError("All features must be finite numbers")

        return values


# ==================================================
# 7. HEALTH ENDPOINT
# ==================================================

@app.get("/health")
async def health():

    return {
        "status": "healthy",
        "model": metadata["model_type"],
        "max_concurrent_inferences": MAX_CONCURRENT_INFERENCES,
    }


# ==================================================
# 8. PREDICTION ENDPOINT
# ==================================================

@app.post("/predict")
async def predict(request: PredictionRequest):

    request_start = time.perf_counter()

    REQUESTS_TOTAL.inc()

    acquired = False

    try:

        # Wait for an available inference slot
        queue_wait = await inference_slots.acquire()

        acquired = True

        QUEUE_WAIT_DURATION.observe(queue_wait)

        # Prepare model input
        features = np.asarray(
            request.features,
            dtype=float,
        ).reshape(1, -1)

        # Execute prediction
        model_start = time.perf_counter()

        prediction = await run_in_threadpool(
            model.predict,
            features,
        )

        model_duration = (
            time.perf_counter() - model_start
        )

        MODEL_EXECUTION_DURATION.observe(
            model_duration
        )

        predicted_class_index = int(prediction[0])

        predicted_class = CLASS_NAMES[
            predicted_class_index
        ]

        return {
            "prediction": predicted_class,
            "class_index": predicted_class_index,
            "model": metadata["model_type"],
        }

    except Exception:

        REQUEST_ERRORS.inc()

        raise

    finally:

        # Release inference capacity
        if acquired:

            await inference_slots.release()

        # Record total request duration
        REQUEST_DURATION.observe(
            time.perf_counter() - request_start
        )


# ==================================================
# 9. PROMETHEUS METRICS ENDPOINT
# ==================================================

@app.get("/metrics")
async def metrics():

    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
