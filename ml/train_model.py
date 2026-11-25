import json
import pickle
from datetime import datetime, timezone
from pathlib import Path

from sklearn.datasets import load_breast_cancer
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
)
from sklearn.model_selection import train_test_split


PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = PROJECT_ROOT / "models"

MODEL_DIR.mkdir(parents=True, exist_ok=True)


def train_model():
    # Load dataset
    dataset = load_breast_cancer()

    X = dataset.data
    y = dataset.target

    # Split dataset
    X_train, X_test, y_train, y_test = train_test_split(
        X,
        y,
        test_size=0.2,
        random_state=42,
        stratify=y,
    )

    # Initialize model
    model = RandomForestClassifier(
        n_estimators=100,
        max_depth=None,
        random_state=42,
        n_jobs=1,
    )

    # Train model
    model.fit(X_train, y_train)

    # Evaluate model
    predictions = model.predict(X_test)

    accuracy = accuracy_score(y_test, predictions)

    print("Model Training Completed")
    print("------------------------")
    print(f"Training samples: {len(X_train)}")
    print(f"Testing samples: {len(X_test)}")
    print(f"Accuracy: {accuracy:.4f}")
    print("\nClassification Report:")
    print(classification_report(y_test, predictions))
    print("Confusion Matrix:")
    print(confusion_matrix(y_test, predictions))

    # Save model
    model_path = MODEL_DIR / "model.pkl"

    with open(model_path, "wb") as file:
        pickle.dump(model, file)

    # Save metadata
    metadata = {
        "model_type": "RandomForestClassifier",
        "dataset": "Breast Cancer Wisconsin",
        "n_estimators": 100,
        "max_depth": None,
        "random_state": 42,
        "n_jobs": 1,
        "training_samples": len(X_train),
        "testing_samples": len(X_test),
        "features": dataset.feature_names.tolist(),
        "feature_count": len(dataset.feature_names),
        "classes": dataset.target_names.tolist(),
        "accuracy": float(accuracy),
        "trained_at_utc": datetime.now(timezone.utc).isoformat(),
    }

    metadata_path = MODEL_DIR / "model_metadata.json"

    with open(metadata_path, "w") as file:
        json.dump(metadata, file, indent=4)

    print(f"\nModel saved to: {model_path}")
    print(f"Metadata saved to: {metadata_path}")


if __name__ == "__main__":
    train_model()

