"""Optional, deterministic CPU work used only for load characterization."""
from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np


class WorkloadConfigurationError(ValueError):
    """Raised when an environment-supplied workload setting is unsafe."""


def _integer_setting(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as error:
        raise WorkloadConfigurationError(f"{name} must be an integer") from error
    if not minimum <= value <= maximum:
        raise WorkloadConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class CpuWorkload:
    """A bounded numerical workload that never affects model input or output."""

    iterations: int
    matrix_size: int
    matrix: np.ndarray | None
    initial_vector: np.ndarray | None

    @classmethod
    def from_environment(cls) -> "CpuWorkload":
        # Zero iterations disables all additional work by default.
        iterations = _integer_setting("CPU_WORKLOAD_ITERATIONS", default=0, minimum=0, maximum=100)
        matrix_size = _integer_setting("CPU_WORKLOAD_MATRIX_SIZE", default=128, minimum=16, maximum=512)
        if iterations == 0:
            return cls(iterations=0, matrix_size=matrix_size, matrix=None, initial_vector=None)

        # Fixed values make the workload deterministic and avoid per-request allocation.
        values = np.arange(matrix_size * matrix_size, dtype=np.float64)
        matrix = ((values.reshape(matrix_size, matrix_size) % 97.0) / 97.0) + np.eye(matrix_size)
        vector = np.linspace(0.0, 1.0, matrix_size, dtype=np.float64)
        return cls(iterations=iterations, matrix_size=matrix_size, matrix=matrix, initial_vector=vector)

    @property
    def enabled(self) -> bool:
        return self.iterations > 0

    def run(self) -> None:
        """Perform bounded matrix-vector operations; discard the deterministic result."""
        if not self.enabled:
            return
        assert self.matrix is not None and self.initial_vector is not None
        vector = self.initial_vector
        for _ in range(self.iterations):
            vector = np.tanh(self.matrix @ vector)
        # Force calculation completion without exposing or using this value in prediction.
        float(vector[0])
