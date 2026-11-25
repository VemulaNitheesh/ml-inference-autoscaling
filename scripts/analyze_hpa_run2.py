import csv
import statistics
from datetime import datetime
from collections import defaultdict

BASE = "results/experiment_3_queue_hpa/run_2"

HPA_FILE = f"{BASE}/hpa_timeline.csv"
POD_FILE = f"{BASE}/pod_timeline.csv"
RESOURCE_FILE = f"{BASE}/resource_usage.csv"


def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_cpu(value):
    value = value.strip()

    if value.endswith("m"):
        return float(value[:-1])

    return float(value) * 1000


def parse_memory(value):
    value = value.strip()

    units = {
        "Ki": 1 / 1024,
        "Mi": 1,
        "Gi": 1024,
        "K": 1 / 1024,
        "M": 1,
        "G": 1024
    }

    for unit, factor in units.items():
        if value.endswith(unit):
            return float(value[:-len(unit)]) * factor

    return float(value) / (1024 * 1024)


def load_hpa():
    rows = []

    with open(HPA_FILE, newline="") as f:
        for row in csv.reader(f):
            if not row:
                continue

            rows.append({
                "timestamp": parse_time(row[0]),
                "current": int(row[1]),
                "desired": int(row[2]),
                "queue": float(row[3])
            })

    return sorted(rows, key=lambda x: x["timestamp"])


def load_pods():
    pods = {}

    with open(POD_FILE, newline="") as f:
        for row in csv.reader(f):
            if not row:
                continue

            timestamp = parse_time(row[0])
            name = row[1]
            phase = row[2]
            ready = row[3].lower() == "true"
            created = parse_time(row[4])

            if name not in pods:
                pods[name] = {
                    "created": created,
                    "first_observed": timestamp,
                    "first_ready": None,
                    "last_observed": timestamp,
                    "phases": set()
                }

            data = pods[name]
            data["last_observed"] = timestamp
            data["phases"].add(phase)

            if ready and data["first_ready"] is None:
                data["first_ready"] = timestamp

    return pods


def load_resources():
    resources = defaultdict(lambda: {
        "cpu": [],
        "memory": []
    })

    with open(RESOURCE_FILE, newline="") as f:
        for row in csv.reader(f):
            if not row:
                continue

            timestamp = parse_time(row[0])
            name = row[1]
            cpu = parse_cpu(row[2])
            memory = parse_memory(row[3])

            resources[name]["cpu"].append(cpu)
            resources[name]["memory"].append(memory)

    return resources


def print_section(title):
    print("\n" + "=" * 75)
    print(title)
    print("=" * 75)


# Load data
hpa = load_hpa()
pods = load_pods()
resources = load_resources()

if not hpa:
    raise SystemExit("No HPA observations found.")

start = hpa[0]["timestamp"]
end = hpa[-1]["timestamp"]
duration = (end - start).total_seconds()

# --------------------------------------------------
# 1. OVERALL EXPERIMENT SUMMARY
# --------------------------------------------------

print_section("1. OVERALL EXPERIMENT SUMMARY")

print("Total HPA observations:", len(hpa))
print("Experiment start:", start)
print("Experiment end:", end)
print("Experiment duration:", round(duration / 60, 2), "minutes")
print("Initial replicas:", hpa[0]["current"])
print("Final replicas:", hpa[-1]["current"])
print("Maximum current replicas:", max(r["current"] for r in hpa))
print("Maximum desired replicas:", max(r["desired"] for r in hpa))

intervals = [
    (hpa[i]["timestamp"] - hpa[i-1]["timestamp"]).total_seconds()
    for i in range(1, len(hpa))
]

if intervals:
    print("Average monitoring interval:",
          round(statistics.mean(intervals), 2), "seconds")

# --------------------------------------------------
# 2. HPA REPLICA TRANSITIONS
# --------------------------------------------------

print_section("2. CURRENT REPLICA TRANSITIONS")

previous = None
current_transitions = []

for row in hpa:
    if row["current"] != previous:
        current_transitions.append(row)
        print(
            row["timestamp"],
            "Replicas:", row["current"]
        )
        previous = row["current"]

print_section("3. DESIRED REPLICA TRANSITIONS")

previous = None
desired_transitions = []

for row in hpa:
    if row["desired"] != previous:
        desired_transitions.append(row)
        print(
            row["timestamp"],
            "Desired replicas:", row["desired"]
        )
        previous = row["desired"]

# --------------------------------------------------
# 3. HPA REACTION DELAYS
# --------------------------------------------------

print_section("4. OBSERVED HPA REACTION DELAYS")

for desired in desired_transitions:
    target = desired["desired"]

    if target == desired["current"]:
        continue

    later = [
        row for row in current_transitions
        if row["timestamp"] > desired["timestamp"]
        and row["current"] == target
    ]

    if later:
        actual = later[0]
        delay = (
            actual["timestamp"] - desired["timestamp"]
        ).total_seconds()

        print(
            f"{desired['timestamp']} | "
            f"{desired['current']} -> {target} | "
            f"{delay:.2f} seconds"
        )
    else:
        print(
            f"{desired['timestamp']} | "
            f"Target {target} not observed afterward"
        )

# --------------------------------------------------
# 4. QUEUE METRIC ANALYSIS
# --------------------------------------------------

print_section("5. QUEUE METRIC ANALYSIS")

queue_values = [r["queue"] for r in hpa]

print("Maximum queue metric:", max(queue_values))
print("Minimum queue metric:", min(queue_values))
print("Average queue metric:", round(statistics.mean(queue_values), 3))
print("Median queue metric:", round(statistics.median(queue_values), 3))
print("Non-zero observations:",
      sum(value > 0 for value in queue_values))
print("Zero-queue observations:",
      sum(value == 0 for value in queue_values))

# --------------------------------------------------
# 5. POD STARTUP ANALYSIS
# --------------------------------------------------

print_section("6. POD STARTUP ANALYSIS")

startup_values = []

for name, data in sorted(pods.items()):
    created = data["created"]

    # Ignore Pods created before experiment start.
    if created < start:
        continue

    print("\nPod:", name)
    print("Creation time:", created)
    print("First observed:", data["first_observed"])
    print("Observed phases:", ", ".join(sorted(data["phases"])))

    ready = data["first_ready"]

    if ready:
        startup = (ready - created).total_seconds()
        startup_values.append(startup)

        print("First observed Ready:", ready)
        print("Observed startup duration:",
              round(startup, 2), "seconds")
    else:
        print("Ready observation unavailable")

if startup_values:
    print("\nStartup summary:")
    print("Average observed startup:",
          round(statistics.mean(startup_values), 2), "seconds")
    print("Maximum observed startup:",
          round(max(startup_values), 2), "seconds")
else:
    print("\nNo newly created Pods with readiness observations found.")

# --------------------------------------------------
# 6. CPU AND MEMORY ANALYSIS
# --------------------------------------------------

print_section("7. RESOURCE UTILIZATION ANALYSIS")

all_cpu = []
all_memory = []

for name, data in sorted(resources.items()):
    cpu_values = data["cpu"]
    memory_values = data["memory"]

    if not cpu_values:
        continue

    all_cpu.extend(cpu_values)
    all_memory.extend(memory_values)

    print("\nPod:", name)
    print("CPU observations:", len(cpu_values))
    print("Average CPU:", round(statistics.mean(cpu_values), 2), "m")
    print("Maximum CPU:", round(max(cpu_values), 2), "m")
    print("Average memory:", round(statistics.mean(memory_values), 2), "Mi")
    print("Maximum memory:", round(max(memory_values), 2), "Mi")

if all_cpu:
    print("\nOverall resource observations:")
    print("Average sampled Pod CPU:",
          round(statistics.mean(all_cpu), 2), "m")
    print("Maximum sampled Pod CPU:",
          round(max(all_cpu), 2), "m")
    print("Average sampled Pod memory:",
          round(statistics.mean(all_memory), 2), "Mi")
    print("Maximum sampled Pod memory:",
          round(max(all_memory), 2), "Mi")

# --------------------------------------------------
# 7. FINAL SUMMARY
# --------------------------------------------------

print_section("8. FINAL EXPERIMENT SUMMARY")

print("Initial replicas:", hpa[0]["current"])
print("Maximum replicas:", max(r["current"] for r in hpa))
print("Final replicas:", hpa[-1]["current"])
print("Maximum queue metric:", max(queue_values))
print("Total Pods observed:", len(pods))
print("New Pods created during experiment:",
      sum(1 for p in pods.values() if p["created"] >= start))
print("Pods with observed startup duration:", len(startup_values))

print("\nAnalysis completed.")
print("Original experiment CSV files were not modified.")
