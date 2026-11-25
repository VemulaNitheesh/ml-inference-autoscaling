import csv
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path("results/experiment_4_queue_keda/run_5")
INTERVAL = 5

HPA_NAME = "keda-hpa-ml-inference-queue-keda"
HPA_FILE = BASE / "keda_timeline.csv"
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
    value = str(value)

    if value.endswith("m"):
        return float(value[:-1]) / 1000

    return float(value)


def read_keda_queue_depth():
    path = (
        "/apis/external.metrics.k8s.io/v1beta1/"
        "namespaces/default/s0-prometheus"
        "?labelSelector=scaledobject.sh%2Fname%3Dml-inference-queue-keda"
    )

    # Use the actual KEDA label selector.
    path = path.replace(
        "scaledobject.sh",
        "scaledobject.keda.sh"
    )

    raw = kubectl("get", "--raw", path)
    data = json.loads(raw)

    items = data.get("items", [])

    if not items:
        raise ValueError("KEDA external metric returned no values")

    return queue_value(items[0]["value"])


def write_row(file_path, row):
    with open(file_path, "a", newline="") as file:
        csv.writer(file).writerow(row)


def collect():
    now = timestamp()

    # KEDA-generated HPA information
    hpa = json.loads(kubectl(
        "get", "hpa", HPA_NAME, "-o", "json"
    ))

    status = hpa.get("status", {})

    current = status.get("currentReplicas", 0)
    desired = status.get("desiredReplicas", 0)

    total_queue = read_keda_queue_depth()

    # Read the average queue value calculated by Kubernetes
    average_queue = None

    for metric in status.get("currentMetrics", []):
        external = metric.get("external", {})
        if external.get("metric", {}).get("name") == "s0-prometheus":
            value = external.get("current", {}).get("averageValue")
            if value is not None:
                average_queue = queue_value(value)
                break

    if average_queue is None:
        raise ValueError("KEDA average queue metric is unavailable")

    write_row(HPA_FILE, [
        now, current, desired, total_queue, average_queue
    ])

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
        f"[{now}] Current={current}, Desired={desired}, "
        f"Total queue={total_queue}, Average queue={average_queue}"
    )


def main():
    BASE.mkdir(parents=True, exist_ok=True)

    print("Starting KEDA experiment monitoring.")
    print(f"Output directory: {BASE}")
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
