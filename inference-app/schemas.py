"""Pydantic schemas for named, finite inference features."""
from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from model_loader import load_metadata

_METADATA = load_metadata()
EXPECTED_FEATURE_NAMES = tuple(_METADATA["feature_names"])
FiniteNumber = Annotated[float, Field(strict=True, allow_inf_nan=False)]


class PredictionRequest(BaseModel):
    """A complete, named feature vector in the model's training order."""

    model_config = ConfigDict(extra="forbid")
    features: dict[str, FiniteNumber]

    @model_validator(mode="after")
    def validate_feature_names(self) -> "PredictionRequest":
        supplied = set(self.features)
        expected = set(EXPECTED_FEATURE_NAMES)
        missing = sorted(expected - supplied)
        unexpected = sorted(supplied - expected)
        if missing or unexpected:
            details = []
            if missing:
                details.append(f"missing features: {', '.join(missing)}")
            if unexpected:
                details.append(f"unexpected features: {', '.join(unexpected)}")
            raise ValueError("; ".join(details))
        return self


class HealthResponse(BaseModel):
    status: str
    model: str | None = None


class PredictionResponse(BaseModel):
    prediction: str
    probabilities: dict[str, float]
