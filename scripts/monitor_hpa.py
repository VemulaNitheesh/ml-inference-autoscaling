import csv
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path("results/experiment_3_queue_hpa/run_5")
INTERVAL = 5

HPA_FILE = BASE / "hpa_timeline.csv"
POD_FILE = BASE / "pod_timeline.csv"
RESOURCE_FILE = BASE / "resource_usage.csv"


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def kubectl(*args):
    result = subprocess.run(
        ["kubectl", *args],
        capture_output=True,
        text=True,
        check=True
    )
    return result.stdout


def queue_value(value):
    """Convert a Kubernetes quantity such as 5 or 500m to a number."""
    value = str(value)

    if value.endswith("m"):
        return float(value[:-1]) / 1000

    return float(value)


def read_queue_depth():
    raw = kubectl(
        "get", "--raw",
        "/apis/custom.metrics.k8s.io/v1beta1/namespaces/default/pods/*/inference_queue_depth"
    )

    data = json.loads(raw)

    return sum(
        queue_value(item["value"])
        for item in data.get("items", [])
    )


def write_row(file_path, row):
    with open(file_path, "a", newline="") as file:
        csv.writer(file).writerow(row)


def collect():
    now = timestamp()

    # HPA and Deployment information
    hpa = json.loads(kubectl(
        "get", "hpa", "ml-inference-queue-hpa", "-o", "json"
    ))

    current = hpa.get("status", {}).get("currentReplicas", 0)
    desired = hpa.get("status", {}).get("desiredReplicas", 0)
    queue = read_queue_depth()

    write_row(HPA_FILE, [now, current, desired, queue])

    # Pod information
    pods = json.loads(kubectl(
        "get", "pods", "-l", "app=ml-inference", "-o", "json"
    ))

    for pod in pods.get("items", []):
        name = pod["metadata"]["name"]
        phase = pod.get("status", {}).get("phase", "Unknown")
        created = pod["metadata"].get("creationTimestamp", "")

        conditions = pod.get("status", {}).get("conditions", [])
        ready = any(
            condition.get("type") == "Ready"
            and condition.get("status") == "True"
            for condition in conditions
        )

        write_row(POD_FILE, [
            now, name, phase, ready, created
        ])

    # CPU and memory usage
    try:
        output = kubectl(
            "top", "pods", "-l", "app=ml-inference", "--no-headers"
        )

        for line in output.strip().splitlines():
            parts = line.split()

            if len(parts) >= 3:
                write_row(RESOURCE_FILE, [
                    now, parts[0], parts[1], parts[2]
                ])

    except subprocess.CalledProcessError:
        print("Resource metrics temporarily unavailable.")

    print(
        f"[{now}] Current replicas={current}, "
        f"Desired replicas={desired}, Queue depth={queue}"
    )


def main():
    BASE.mkdir(parents=True, exist_ok=True)

    print("Starting HPA experiment monitoring.")
    print(f"Sampling interval: {INTERVAL} seconds")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            try:
                collect()
            except Exception as error:
                print(f"Collection error: {error}")

            time.sleep(INTERVAL)

    except KeyboardInterrupt:
        print("\nMonitoring stopped.")


if __name__ == "__main__":
    main()
