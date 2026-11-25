import csv
import statistics
from pathlib import Path
from datetime import datetime

BASE = Path("results/experiment_4_queue_keda")
RUNS = [BASE / f"run_{i}" for i in range(1, 6)]


def read_csv(path):
    if not path.exists():
        raise FileNotFoundError(path)

    with path.open() as f:
        return list(csv.reader(f))


def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_cpu(value):
    value = str(value)
    if value.endswith("m"):
        return float(value[:-1])
    return float(value) * 1000


def parse_memory(value):
    value = str(value)

    units = {
        "Ki": 1 / 1024,
        "Mi": 1,
        "Gi": 1024,
        "Ti": 1024 * 1024,
    }

    for unit, multiplier in units.items():
        if value.endswith(unit):
            return float(value[:-len(unit)]) * multiplier

    if value.endswith("K"):
        return float(value[:-1]) / 1024
    if value.endswith("M"):
        return float(value[:-1])
    if value.endswith("G"):
        return float(value[:-1]) * 1024

    return float(value)


def analyze_run(run_dir):
    name = run_dir.name

    timeline = read_csv(run_dir / "keda_timeline.csv")
    pods = read_csv(run_dir / "pod_timeline.csv")
    resources = read_csv(run_dir / "resource_usage.csv")

    if not timeline:
        raise ValueError(f"{name}: timeline is empty")

    rows = [{
        "time": parse_time(r[0]),
        "current": int(r[1]),
        "desired": int(r[2]),
        "total": float(r[3]),
        "average": float(r[4]),
    } for r in timeline]

    start = rows[0]["time"]
    end = rows[-1]["time"]
    duration = (end - start).total_seconds() / 60
    intervals = [
        (rows[i]["time"] - rows[i-1]["time"]).total_seconds()
        for i in range(1, len(rows))
    ]

    current_transitions = []
    desired_transitions = []

    previous = None
    for r in rows:
        if r["current"] != previous:
            current_transitions.append((r["time"], r["current"]))
            previous = r["current"]

    previous = None
    for r in rows:
        if r["desired"] != previous:
            desired_transitions.append((r["time"], r["desired"]))
            previous = r["desired"]

    # Match each desired replica change with the next observed
    # current-replica transition reaching that target.
    delays = []
    used_current = set()

    for desired_time, target in desired_transitions[1:]:
        for index, (current_time, current_value) in enumerate(
            current_transitions
        ):
            if (
                index not in used_current
                and current_time >= desired_time
                and current_value == target
            ):
                delays.append(
                    (current_time - desired_time).total_seconds()
                )
                used_current.add(index)
                break

    # Pod startup times: new Pods created during this run.
    pod_data = {}

    for r in pods:
        timestamp, pod_name, phase, ready, created = r
        if pod_name not in pod_data:
            pod_data[pod_name] = {
                "created": parse_time(created),
                "ready_times": [],
            }

        if ready.lower() == "true":
            pod_data[pod_name]["ready_times"].append(
                parse_time(timestamp)
            )

    startup_times = []

    for pod_name, data in pod_data.items():
        created = data["created"]

        if start <= created <= end and data["ready_times"]:
            first_ready = min(data["ready_times"])
            startup = (first_ready - created).total_seconds()
            startup_times.append(startup)

    # Resource usage: pooled Pod samples.
    cpu_values = []
    memory_values = []

    for r in resources:
        cpu_values.append(parse_cpu(r[2]))
        memory_values.append(parse_memory(r[3]))

    result = {
        "run": name,
        "observations": len(rows),
        "duration": duration,
        "interval": statistics.mean(intervals) if intervals else 0,
        "initial": rows[0]["current"],
        "final": rows[-1]["current"],
        "max_current": max(r["current"] for r in rows),
        "max_desired": max(r["desired"] for r in rows),
        "max_total_queue": max(r["total"] for r in rows),
        "avg_total_queue": statistics.mean(r["total"] for r in rows),
        "max_average_queue": max(r["average"] for r in rows),
        "avg_average_queue": statistics.mean(r["average"] for r in rows),
        "nonzero_queue": sum(r["total"] > 0 for r in rows),
        "current_transitions": current_transitions,
        "desired_transitions": desired_transitions,
        "delays": delays,
        "avg_delay": statistics.mean(delays) if delays else 0,
        "startup_times": startup_times,
        "avg_startup": statistics.mean(startup_times) if startup_times else 0,
        "max_startup": max(startup_times) if startup_times else 0,
        "cpu_samples": len(cpu_values),
        "avg_cpu": statistics.mean(cpu_values) if cpu_values else 0,
        "max_cpu": max(cpu_values) if cpu_values else 0,
        "avg_memory": statistics.mean(memory_values) if memory_values else 0,
        "max_memory": max(memory_values) if memory_values else 0,
    }

    return result


def show_run(r):
    print("\n" + "=" * 65)
    print(f"{r['run'].upper()} SUMMARY")
    print("=" * 65)

    print(f"Observations:             {r['observations']}")
    print(f"Duration:                 {r['duration']:.2f} minutes")
    print(f"Average sampling interval:{r['interval']:.2f} seconds")
    print(f"Initial replicas:         {r['initial']}")
    print(f"Final replicas:           {r['final']}")
    print(f"Maximum current replicas: {r['max_current']}")
    print(f"Maximum desired replicas: {r['max_desired']}")

    print(f"\nMaximum total queue:      {r['max_total_queue']:.2f}")
    print(f"Average total queue:      {r['avg_total_queue']:.2f}")
    print(f"Maximum average queue:    {r['max_average_queue']:.2f}")
    print(f"Average average queue:    {r['avg_average_queue']:.2f}")
    print(f"Non-zero queue samples:   {r['nonzero_queue']}")

    print("\nCurrent replica transitions:")
    for timestamp, value in r["current_transitions"]:
        print(f"  {timestamp.isoformat()} -> {value}")

    print("\nDesired replica transitions:")
    for timestamp, value in r["desired_transitions"]:
        print(f"  {timestamp.isoformat()} -> {value}")

    print("\nObserved reaction delays (seconds):")
    print(" ", [round(x, 2) for x in r["delays"]])
    print(f"Average reaction delay:   {r['avg_delay']:.2f} seconds")

    print("\nPod startup:")
    print("  Startup times:", [round(x, 2) for x in r["startup_times"]])
    print(f"  Average startup:        {r['avg_startup']:.2f} seconds")
    print(f"  Maximum startup:        {r['max_startup']:.2f} seconds")

    print("\nResource usage:")
    print(f"  CPU samples:            {r['cpu_samples']}")
    print(f"  Average CPU:            {r['avg_cpu']:.2f} millicores")
    print(f"  Maximum CPU:            {r['max_cpu']:.2f} millicores")
    print(f"  Average memory:         {r['avg_memory']:.2f} MiB")
    print(f"  Maximum memory:         {r['max_memory']:.2f} MiB")


def main():
    results = []

    for run_dir in RUNS:
        if not run_dir.exists():
            raise FileNotFoundError(f"Missing directory: {run_dir}")

        result = analyze_run(run_dir)
        results.append(result)
        show_run(result)

    print("\n" + "=" * 95)
    print("CONSOLIDATED KEDA COMPARISON: ALL FIVE RUNS")
    print("=" * 95)

    headers = (
        "Run", "Duration", "Max Replicas", "Final",
        "Max Queue", "Avg Queue", "Reaction", "Startup",
        "Avg CPU", "Avg Memory"
    )

    print(
        f"{headers[0]:<8}{headers[1]:>10}{headers[2]:>14}"
        f"{headers[3]:>8}{headers[4]:>11}{headers[5]:>11}"
        f"{headers[6]:>12}{headers[7]:>11}"
        f"{headers[8]:>12}{headers[9]:>13}"
    )

    for r in results:
        print(
            f"{r['run']:<8}"
            f"{r['duration']:>10.2f}"
            f"{r['max_current']:>14}"
            f"{r['final']:>8}"
            f"{r['max_total_queue']:>11.2f}"
            f"{r['avg_total_queue']:>11.2f}"
            f"{r['avg_delay']:>12.2f}"
            f"{r['avg_startup']:>11.2f}"
            f"{r['avg_cpu']:>12.2f}"
            f"{r['avg_memory']:>13.2f}"
        )

    print("\nOVERALL FIVE-RUN AVERAGES")

    metrics = [
        ("Maximum replicas", "max_current"),
        ("Average total queue", "avg_total_queue"),
        ("Average reaction delay (sec)", "avg_delay"),
        ("Average Pod startup (sec)", "avg_startup"),
        ("Average CPU (millicores)", "avg_cpu"),
        ("Average memory (MiB)", "avg_memory"),
    ]

    for label, key in metrics:
        values = [r[key] for r in results if r[key] is not None]
        print(f"{label:<35}: {statistics.mean(values):.2f}")

    print("\nNote:")
    print("- Resource averages are pooled across recorded Pod samples.")
    print("- Reaction delay is desired-to-observed-current transition delay.")
    print("- Startup time is measured from Pod creation to first observed Ready.")
    print("- Sampling intervals limit timing precision.")
    print("- These are descriptive results, not statistical significance tests.")


if __name__ == "__main__":
    main()
