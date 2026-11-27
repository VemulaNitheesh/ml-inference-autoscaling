#!/usr/bin/env python3
"""Train, evaluate, save, and independently validate a simple ML classifier.

The model is intentionally uncomplicated: it is the inference workload for later
Kubernetes autoscaling experiments, not the subject of hyperparameter research.
"""
from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
from sklearn.datasets import load_breast_cancer
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
)
from sklearn.model_selection import train_test_split

RANDOM_STATE = 42
TEST_SIZE = 0.20
MODEL_PARAMETERS = {
    "n_estimators": 100,
    "max_depth": None,
    "random_state": RANDOM_STATE,
    "n_jobs": -1,
}
BASE_DIR = Path(__file__).resolve().parent
MODELS_DIR = BASE_DIR / "models"
MODEL_PATH = MODELS_DIR / "model.pkl"
METADATA_PATH = MODELS_DIR / "model_metadata.json"


def print_dataset_summary(dataset) -> None:
    class_distribution = {
        dataset.target_names[index]: int(count)
        for index, count in enumerate(np.bincount(dataset.target))
    }
    print("Dataset: Breast Cancer Wisconsin Diagnostic Dataset")
    print(f"Samples: {dataset.data.shape[0]}")
    print(f"Features: {dataset.data.shape[1]}")
    print(f"Target classes: {list(dataset.target_names)}")
    print(f"Class distribution: {class_distribution}")
    print("Feature names:")
    for feature_name in dataset.feature_names:
        print(f"  - {feature_name}")


def main() -> None:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    # Random Forest does not require feature scaling; raw numeric features are used.
    dataset = load_breast_cancer()
    X, y = dataset.data, dataset.target
    print_dataset_summary(dataset)

    X_train, X_test, y_train, y_test = train_test_split(
        X,
        y,
        test_size=TEST_SIZE,
        stratify=y,
        random_state=RANDOM_STATE,
    )
    print(f"\nTrain samples: {len(X_train)}")
    print(f"Test samples: {len(X_test)}")

    model = RandomForestClassifier(**MODEL_PARAMETERS)
    model.fit(X_train, y_train)

    predictions = model.predict(X_test)
    metrics = {
        "accuracy": float(accuracy_score(y_test, predictions)),
        "precision_weighted": float(
            precision_score(y_test, predictions, average="weighted", zero_division=0)
        ),
        "recall_weighted": float(
            recall_score(y_test, predictions, average="weighted", zero_division=0)
        ),
        "f1_weighted": float(
            f1_score(y_test, predictions, average="weighted", zero_division=0)
        ),
        "confusion_matrix": confusion_matrix(y_test, predictions).tolist(),
        "classification_report": classification_report(
            y_test,
            predictions,
            target_names=dataset.target_names,
            output_dict=True,
            zero_division=0,
        ),
    }

    print("\nEvaluation results")
    print(f"Accuracy: {metrics['accuracy']:.4f}")
    print(f"Weighted precision: {metrics['precision_weighted']:.4f}")
    print(f"Weighted recall: {metrics['recall_weighted']:.4f}")
    print(f"Weighted F1-score: {metrics['f1_weighted']:.4f}")
    print("Confusion matrix:")
    print(np.asarray(metrics["confusion_matrix"]))
    print("Classification report:")
    print(classification_report(y_test, predictions, target_names=dataset.target_names, zero_division=0))

    joblib.dump(model, MODEL_PATH)
    metadata = {
        "model_name": "breast_cancer_random_forest",
        "algorithm": "RandomForestClassifier",
        "dataset_name": "Breast Cancer Wisconsin Diagnostic Dataset",
        "dataset_samples": int(X.shape[0]),
        "training_samples": int(X_train.shape[0]),
        "test_samples": int(X_test.shape[0]),
        "number_of_features": int(X.shape[1]),
        "feature_names": list(dataset.feature_names),
        "target_class_names": list(dataset.target_names),
        "class_distribution": {
            dataset.target_names[index]: int(count)
            for index, count in enumerate(np.bincount(y))
        },
        "train_test_split": {
            "test_size": TEST_SIZE,
            "stratified": True,
            "random_state": RANDOM_STATE,
        },
        "training_parameters": MODEL_PARAMETERS,
        "random_state": RANDOM_STATE,
        "evaluation_metrics": metrics,
    }
    METADATA_PATH.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"Saved model: {MODEL_PATH}")
    print(f"Saved metadata: {METADATA_PATH}")

    # Independent persistence check: reload from disk and use a held-out sample.
    reloaded_model = joblib.load(MODEL_PATH)
    sample = X_test[0].reshape(1, -1)
    predicted_index = int(reloaded_model.predict(sample)[0])
    probabilities = reloaded_model.predict_proba(sample)[0]
    if probabilities.shape[0] != len(dataset.target_names) or not np.isclose(probabilities.sum(), 1.0):
        raise RuntimeError("Reloaded model produced an invalid probability vector")

    print("\nSaved-model validation")
    print(f"Sample true class: {dataset.target_names[int(y_test[0])]}")
    print(f"Sample predicted class: {dataset.target_names[predicted_index]}")
    print("Class probabilities:")
    for class_name, probability in zip(dataset.target_names, probabilities):
        print(f"  {class_name}: {probability:.6f}")
    print("Saved model reload and prediction validation: PASSED")


if __name__ == "__main__":
    main()
