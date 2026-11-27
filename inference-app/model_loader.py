"""Model and metadata loading for the inference API."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import joblib

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = PROJECT_ROOT / "ml-model" / "models" / "model.pkl"
METADATA_PATH = PROJECT_ROOT / "ml-model" / "models" / "model_metadata.json"


@dataclass(frozen=True)
class ModelBundle:
    """A loaded estimator together with the immutable feature schema."""

    model: object
    model_name: str
    feature_names: tuple[str, ...]
    target_class_names: tuple[str, ...]


def load_metadata() -> dict:
    """Load metadata only; used to define the API request schema."""
    try:
        with METADATA_PATH.open(encoding="utf-8") as file:
            metadata = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("Unable to read model metadata") from error

    required = {"algorithm", "number_of_features", "feature_names", "target_class_names"}
    missing = required.difference(metadata)
    if missing:
        raise RuntimeError(f"Model metadata is missing required fields: {sorted(missing)}")
    if len(metadata["feature_names"]) != metadata["number_of_features"]:
        raise RuntimeError("Model metadata feature count does not match feature names")
    return metadata


def load_model_bundle() -> ModelBundle:
    """Load the saved estimator once and validate its schema against metadata."""
    metadata = load_metadata()
    try:
        model = joblib.load(MODEL_PATH)
    except OSError as error:
        raise RuntimeError("Unable to load trained model") from error

    feature_names = tuple(metadata["feature_names"])
    class_names = tuple(metadata["target_class_names"])
    if getattr(model, "n_features_in_", None) != len(feature_names):
        raise RuntimeError("Saved model feature count does not match metadata")
    if not hasattr(model, "predict") or not hasattr(model, "predict_proba"):
        raise RuntimeError("Saved model does not support prediction and probability prediction")
    if not hasattr(model, "classes_") or len(model.classes_) != len(class_names):
        raise RuntimeError("Saved model classes do not match metadata")

    return ModelBundle(
        model=model,
        model_name=str(metadata["algorithm"]),
        feature_names=feature_names,
        target_class_names=class_names,
    )
