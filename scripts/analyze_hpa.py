import csv
from datetime import datetime

path = "results/experiment_3_queue_hpa/logs/hpa_timeline.csv"

rows = []

with open(path, newline="") as f:
    reader = csv.reader(f)

    for row in reader:
        if not row:
            continue

        rows.append({
            "timestamp": datetime.fromisoformat(row[0]),
            "current_replicas": int(row[1]),
            "desired_replicas": int(row[2]),
            "queue_metric": float(row[3])
        })

if not rows:
    raise SystemExit("No HPA observations found.")

rows.sort(key=lambda r: r["timestamp"])

print("\nHPA EXPERIMENT ANALYSIS")
print("=" * 55)

print("Total observations:", len(rows))
print("Experiment duration:",
      round((rows[-1]["timestamp"] - rows[0]["timestamp"]).total_seconds() / 60, 2), "minutes")
print("Initial replicas:", rows[0]["current_replicas"])
print("Final replicas:", rows[-1]["current_replicas"])
print("Maximum observed replicas:",
      max(r["current_replicas"] for r in rows))
print("Maximum desired replicas:",
      max(r["desired_replicas"] for r in rows))
print("Maximum queue metric:",
      max(r["queue_metric"] for r in rows))

print("\nCURRENT REPLICA TRANSITIONS")
print("-" * 55)

previous = None

for row in rows:
    current = row["current_replicas"]

    if current != previous:
        print(row["timestamp"], "Replicas:", current)
        previous = current

print("\nDESIRED REPLICA TRANSITIONS")
print("-" * 55)

previous = None

for row in rows:
    desired = row["desired_replicas"]

    if desired != previous:
        print(row["timestamp"], "Desired:", desired)
        previous = desired

print("\nQUEUE METRIC SUMMARY")
print("-" * 55)
print("Average queue metric:",
      round(sum(r["queue_metric"] for r in rows) / len(rows), 3))
print("Non-zero queue observations:",
      sum(r["queue_metric"] > 0 for r in rows))
