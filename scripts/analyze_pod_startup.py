import csv
from datetime import datetime, timezone

path = "results/experiment_3_queue_hpa/logs/pod_timeline.csv"

pods = {}

with open(path) as f:
    for row in csv.DictReader(f):
        name = row["pod_name"]

        if name not in pods:
            pods[name] = {
                "created": datetime.fromisoformat(
                    row["creation_timestamp"].replace("Z", "+00:00")
                ),
                "first_ready": None,
                "first_observed": None
            }

        observed = datetime.fromisoformat(row["timestamp"])

        if pods[name]["first_observed"] is None:
            pods[name]["first_observed"] = observed

        if row["ready"].lower() == "true":
            if pods[name]["first_ready"] is None:
                pods[name]["first_ready"] = observed

print("\nPOD STARTUP ANALYSIS")
print("-" * 65)

for name, data in pods.items():
    created = data["created"]
    ready = data["first_ready"]

    print("\nPod:", name)

    if ready:
        startup = (ready - created).total_seconds()
        print("Creation time:", created)
        print("First observed Ready:", ready)
        print("Observed startup time:", round(startup, 2), "seconds")
    else:
        print("Ready observation unavailable")
