"""Automated autoscaler runs for one scaler (default: CPU HPA).
 
For each load (normal, bursty) x 3 runs:
  1. precheck: 1 idle replica, HPA config as expected, /health /predict /metrics OK
  2. record cluster state and run Locust
  3. keep recording until the Deployment is back to 1 pod
  4. save raw data and summary.json
test_aggregate then writes mean/std/CV and pooled percentiles per load.
 
Run from the project root:
    pytest -v -s tests/test_scaler_runs.py
    pytest -v -s tests/test_scaler_runs.py -k normal-run1      # one run only
"""
import csv
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
 
import pytest
 
ROOT = Path(__file__).resolve().parents[1]
SCALER = os.environ.get("SCALER", "cpu-hpa")
HPA_NAME = os.environ.get("HPA_NAME", "ml-inference-keda" if SCALER == "keda" else "ml-inference-cpu")
SCALEDOBJECT_NAME = os.environ.get("SCALEDOBJECT_NAME", "ml-inference-keda")
PROM_URL = os.environ.get("PROM_URL", "http://localhost:9090")  # optional: for the in-flight total
DEPLOYMENT = "ml-inference"
LABEL = "app=ml-inference"
RESULTS = ROOT / "results" / "runs" / SCALER
 
LOADS = ["normal", "bursty"]
RUNS = 3
SLO_MS = 500
SAMPLE_S = 2.0
REQUEST_CPU_M = 250
IDLE_CPU_M = 50                 # total pod CPU must be below this before a run starts
SETTLE_TIMEOUT_S = 900
EXPECTED = {"min": 1, "max": 4, "iterations": "50", "matrix_size": "256"}
 
NORMAL = {"users": 40, "spawn": 4, "seconds": 240}
BURST = {"base": 4, "spike": 40, "lead": 180, "spike_s": 60, "gap": 180, "spikes": 3, "tail": 180, "spawn": 50}
 
REQUEST_LOGGER = '''import csv
import os
import time
 
from locust import events
 
_file = None
_writer = None
 
 
@events.test_start.add_listener
def _open(environment, **kwargs):
    global _file, _writer
    _file = open(os.path.join(os.environ["RUN_DIR"], "requests.csv"), "w", newline="", buffering=1)
    _writer = csv.writer(_file)
    _writer.writerow(["timestamp", "latency_ms", "success", "status"])
 
 
@events.request.add_listener
def _on_request(request_type, name, response_time, response_length, response, context, exception, **kwargs):
    status = response.status_code if response is not None else 0
    ok = int(exception is None and 0 < status < 400)
    _writer.writerow([f"{time.time() - response_time / 1000:.3f}", f"{response_time:.1f}", ok, status])
 
 
@events.test_stop.add_listener
def _close(environment, **kwargs):
    if _file:
        _file.close()
'''
 
SHAPE_TEMPLATE = '''from locust import LoadTestShape
 
STAGES = __STAGES__  # (end_second, users)
SPAWN_RATE = __SPAWN__
 
 
class BurstShape(LoadTestShape):
    def tick(self):
        t = self.get_run_time()
        for end, users in STAGES:
            if t < end:
                return users, SPAWN_RATE
        return None
'''
 
 
# ---------------------------------------------------------------- helpers
def sh(*cmd, timeout=60):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except subprocess.TimeoutExpired:
        return ""
 
 
def kube_json(*args):
    try:
        r = subprocess.run(["kubectl", *args, "-o", "json"], capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return None
    return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None
 
 
def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
 
 
def cpu_m(s):
    return float(s[:-1]) if s.endswith("m") else float(s) * 1000.0
 
 
def mem_mi(s):
    for unit, factor in (("Ki", 1 / 1024), ("Mi", 1.0), ("Gi", 1024.0)):
        if s.endswith(unit):
            return float(s[: -len(unit)]) * factor
    return float(s) / 2**20
 
 
def service_url():
    return os.environ.get("URL") or f"http://{sh('minikube', 'ip').strip()}:30080"
 
 
def http_get(path, timeout=5):
    with urllib.request.urlopen(service_url() + path, timeout=timeout) as r:
        return r.status, r.read().decode()
 
 
def http_predict(timeout=10):
    req = urllib.request.Request(
        service_url() + "/predict", data=(ROOT / "load" / "payload.json").read_bytes(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode()
 
 
def live_pods():
    return (kube_json("get", "pods", "-l", LABEL) or {"items": []})["items"]
 
 
def pod_ready_time(pod):
    for c in pod.get("status", {}).get("conditions", []):
        if c["type"] == "Ready" and c["status"] == "True":
            return parse_ts(c["lastTransitionTime"])
    return None
 
 
def metric_number(value):
    """Convert a Kubernetes resource quantity to a numeric value."""
    if value is None:
        return None
    value = str(value)
    if value.endswith("m"):
        return float(value[:-1]) / 1000.0
    return float(value)


def hpa_state():
    h = kube_json("get", "hpa", HPA_NAME)
    if not h:
        return None

    st = h.get("status", {})
    cpu = None
    metric_type = None
    metric_current = None
    metric_target = None

    for m in st.get("currentMetrics") or []:
        metric_type = m.get("type")

        if metric_type == "Resource":
            resource = m.get("resource", {})
            current = resource.get("current", {})
            cpu = current.get("averageUtilization")
            metric_current = cpu

            for spec_metric in h["spec"].get("metrics", []):
                if spec_metric.get("type") == "Resource":
                    metric_target = spec_metric.get("resource", {}).get(
                        "target", {}
                    ).get("averageUtilization")

        elif metric_type == "External":
            external = m.get("external", {})
            current = external.get("current", {})
            metric_current = metric_number(
                current.get("averageValue", current.get("value"))
            )

            metric_name = external.get("metric", {}).get("name")

            for spec_metric in h["spec"].get("metrics", []):
                if spec_metric.get("type") != "External":
                    continue

                spec_external = spec_metric.get("external", {})
                if spec_external.get("metric", {}).get("name") == metric_name:
                    target = spec_external.get("target", {})
                    metric_target = metric_number(
                        target.get("averageValue", target.get("value"))
                    )
                    break

    return {
        "current": st.get("currentReplicas"),
        "desired": st.get("desiredReplicas"),
        "cpu_pct": cpu,
        "metric_type": metric_type,
        "metric_current": metric_current,
        "metric_target": metric_target,
        "spec": h["spec"],
    }
 
 
def top_pods():
    rows = {}
    for line in sh("kubectl", "top", "pods", "-l", LABEL, "--no-headers").splitlines():
        p = line.split()
        if len(p) >= 3:
            rows[p[0]] = (cpu_m(p[1]), mem_mi(p[2]))
    return rows
 
 
def prom_in_flight():
    try:
        q = urllib.parse.quote('sum(inference_requests_in_flight{job="ml-inference"})')
        with urllib.request.urlopen(f"{PROM_URL}/api/v1/query?query={q}", timeout=2) as r:
            result = json.load(r)["data"]["result"]
        return float(result[0]["value"][1]) if result else 0.0
    except Exception:
        return None
 
 
def pct(values, q):
    s = sorted(values)
    if not s:
        return None
    k = (len(s) - 1) * q / 100
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)
 
 
def read_requests(run_dir):
    with open(Path(run_dir) / "requests.csv") as f:
        rows = list(csv.DictReader(f))
    return ([float(r["timestamp"]) for r in rows], [float(r["latency_ms"]) for r in rows],
            [int(r["success"]) for r in rows])
 
 
# ---------------------------------------------------------------- precheck
def check_cluster():
    """Return a list of problems; an empty list means the cluster is ready for a run."""
    dep, hpa, pods = kube_json("get", "deployment", DEPLOYMENT), hpa_state(), live_pods()
    if not dep or not hpa:
        return ["deployment or HPA not found"]
    problems = []
    if len(pods) != 1 or "deletionTimestamp" in pods[0]["metadata"] or not pod_ready_time(pods[0]):
        problems.append(f"expected exactly 1 ready pod, found {len(pods)}")
    if (dep["status"].get("readyReplicas") or 0) != 1:
        problems.append("deployment readyReplicas != 1")
    if hpa["current"] != 1 or hpa["desired"] != 1:
        problems.append(f"HPA current/desired = {hpa['current']}/{hpa['desired']}")
    top = top_pods()
    if not top:
        problems.append("no pod metrics yet")
    elif sum(c for c, _ in top.values()) > IDLE_CPU_M:
        problems.append(f"pod CPU {sum(c for c, _ in top.values()):.0f}m is not idle (limit {IDLE_CPU_M}m)")
    spec = hpa["spec"]
    if (spec["minReplicas"], spec["maxReplicas"]) != (EXPECTED["min"], EXPECTED["max"]):
        problems.append(f"HPA min/max = {spec['minReplicas']}/{spec['maxReplicas']}")

    if SCALER == "keda":
        so = kube_json("get", "scaledobject", SCALEDOBJECT_NAME)

        if not so:
            problems.append(f"ScaledObject {SCALEDOBJECT_NAME} not found")
        else:
            conditions = {
                c["type"]: c["status"]
                for c in so.get("status", {}).get("conditions", [])
            }
            if conditions.get("Ready") != "True":
                problems.append(f"ScaledObject is not Ready: {conditions}")

            triggers = so.get("spec", {}).get("triggers", [])
            prometheus_trigger = next(
                (t for t in triggers if t.get("type") == "prometheus"), None
            )

            if not prometheus_trigger:
                problems.append("ScaledObject has no Prometheus trigger")
            else:
                metadata = prometheus_trigger.get("metadata", {})
                if metadata.get("threshold") != "5":
                    problems.append(
                        f"KEDA threshold is {metadata.get('threshold')}, expected 5"
                    )

        external_metrics = [
            m for m in spec.get("metrics", [])
            if m.get("type") == "External"
        ]
        if not external_metrics:
            problems.append("KEDA HPA has no External metric")

    inflight = prom_in_flight()
    if inflight is None:
        problems.append("Prometheus in-flight query failed; check PROM_URL")
    elif inflight > 0:
        problems.append(f"Prometheus reports {inflight} in-flight requests; expected idle")
    env = {e["name"]: e.get("value") for e in dep["spec"]["template"]["spec"]["containers"][0].get("env", [])}
    if (env.get("CPU_WORKLOAD_ITERATIONS"), env.get("CPU_WORKLOAD_MATRIX_SIZE")) != (EXPECTED["iterations"], EXPECTED["matrix_size"]):
        problems.append(f"CPU workload env is {env}")
    try:
        code, body = http_get("/health")
        if code != 200 or json.loads(body).get("status") != "healthy":
            problems.append(f"/health -> {code} {body}")
        code, body = http_predict()
        if code != 200 or "prediction" not in body:
            problems.append(f"/predict -> {code} {body}")
        code, metrics_text = http_get("/metrics")
        metric_lines = [
            line for line in metrics_text.splitlines()
            if line.startswith("inference_requests_in_flight")
        ]
        if not metric_lines:
            problems.append("in-flight gauge missing from /metrics")
    except Exception as exc:
        problems.append(f"HTTP check failed: {exc}")
    return problems
 
 
def precheck():
    deadline = time.time() + SETTLE_TIMEOUT_S
    problems = check_cluster()
    while problems and time.time() < deadline:
        print(f"  waiting for clean start: {problems}")
        time.sleep(10)
        problems = check_cluster()
    assert not problems, f"precheck failed: {problems}"
 
 
# ---------------------------------------------------------------- recording
class Collector(threading.Thread):
    FIELDS = [
        "epoch", "hpa_current", "hpa_desired", "hpa_cpu_pct",
        "hpa_metric_type", "hpa_metric_current", "hpa_metric_target",
        "pods_total", "pods_ready", "cpu_m_total", "mem_mi_total",
        "inflight_total"
    ]
 
    def __init__(self, run_dir):
        super().__init__(daemon=True)
        self.run_dir = run_dir
        self.halt = threading.Event()
        self.rows = []
        self.pods_seen = {}
 
    def run(self):
        with open(self.run_dir / "timeseries.csv", "w", newline="") as f_ts, \
                open(self.run_dir / "pods.csv", "w", newline="") as f_pods:
            w_ts, w_pods = csv.writer(f_ts), csv.writer(f_pods)
            w_ts.writerow(self.FIELDS)
            w_pods.writerow(["epoch", "pod", "ready", "terminating", "cpu_m", "mem_mi"])
            while not self.halt.is_set():
                started = time.time()
                try:
                    self.sample(started, w_ts, w_pods)
                    f_ts.flush()
                    f_pods.flush()
                except Exception as exc:  # keep recording even if one sample fails
                    print(f"  collector warning: {exc}")
                self.halt.wait(max(0.0, SAMPLE_S - (time.time() - started)))
 
    def sample(self, t, w_ts, w_pods):
        hpa = hpa_state() or {}
        top = top_pods()
        total = ready = 0
        cpu = mem = 0.0
        for p in live_pods():
            name = p["metadata"]["name"]
            terminating = "deletionTimestamp" in p["metadata"]
            ready_at = pod_ready_time(p)
            rec = self.pods_seen.setdefault(
                name, {"created": parse_ts(p["metadata"]["creationTimestamp"]), "ready": None, "terminating": None})
            if ready_at and rec["ready"] is None:
                rec["ready"] = ready_at
            if terminating and rec["terminating"] is None:
                rec["terminating"] = t
            c, m = top.get(name, (0.0, 0.0))
            w_pods.writerow([f"{t:.3f}", name, int(bool(ready_at)), int(terminating), c, m])
            cpu += c
            mem += m
            if not terminating:
                total += 1
                ready += int(bool(ready_at))
        d = {
            "epoch": t,
            "hpa_current": hpa.get("current"),
            "hpa_desired": hpa.get("desired"),
            "hpa_cpu_pct": hpa.get("cpu_pct"),
            "hpa_metric_type": hpa.get("metric_type"),
            "hpa_metric_current": hpa.get("metric_current"),
            "hpa_metric_target": hpa.get("metric_target"),
            "pods_total": total,
            "pods_ready": ready,
            "cpu_m_total": round(cpu, 1),
            "mem_mi_total": round(mem, 1),
            "inflight_total": prom_in_flight(),
        }
        w_ts.writerow([f"{v:.3f}" if k == "epoch" else v for k, v in d.items()])
        self.rows.append(d)
 
    def stop(self):
        self.halt.set()
        self.join(timeout=30)
        with open(self.run_dir / "pod_lifecycle.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["pod", "created_epoch", "ready_epoch", "terminating_seen_epoch", "startup_s"])
            for name, r in self.pods_seen.items():
                startup = (r["ready"] - r["created"]) if r["ready"] else ""
                w.writerow([name, r["created"], r["ready"] or "", r["terminating"] or "", startup])
 
 
def wait_until_settled(collector, timeout):
    """Block until 1 pod / HPA 1 / desired 1 holds for 3 consecutive samples; return when it began."""
    deadline = time.time() + timeout
    streak, began, seen = 0, None, len(collector.rows)
    while time.time() < deadline:
        time.sleep(SAMPLE_S)
        while seen < len(collector.rows):
            d = collector.rows[seen]
            seen += 1
            if d["pods_total"] == 1 and d["pods_ready"] == 1 and d["hpa_current"] == 1 and d["hpa_desired"] == 1:
                if streak == 0:
                    began = d["epoch"]
                streak += 1
            else:
                streak, began = 0, None
        if streak >= 3:
            return began
    raise AssertionError("replicas did not return to 1 within the timeout")
 
 
# ---------------------------------------------------------------- load definitions
def step_times(load):
    if load == "normal":
        return [0.0]
    return [BURST["lead"] + i * (BURST["spike_s"] + BURST["gap"]) for i in range(BURST["spikes"])]
 
 
def last_spike_end():
    return BURST["lead"] + BURST["spikes"] * BURST["spike_s"] + (BURST["spikes"] - 1) * BURST["gap"]
 
 
def burst_stages():
    stages, t = [], BURST["lead"]
    stages.append((t, BURST["base"]))
    for i in range(BURST["spikes"]):
        t += BURST["spike_s"]
        stages.append((t, BURST["spike"]))
        if i < BURST["spikes"] - 1:
            t += BURST["gap"]
            stages.append((t, BURST["base"]))
    stages.append((t + BURST["tail"], BURST["base"]))
    return stages
 
 
def run_locust(load, run_dir):
    (run_dir / "request_logger.py").write_text(REQUEST_LOGGER)
    files = [ROOT / "load" / "locustfile.py", run_dir / "request_logger.py"]
    extra = []
    if load == "normal":
        extra = ["-u", str(NORMAL["users"]), "-r", str(NORMAL["spawn"]), "-t", f"{NORMAL['seconds']}s"]
        duration = NORMAL["seconds"]
    else:
        stages = burst_stages()
        (run_dir / "shape.py").write_text(
            SHAPE_TEMPLATE.replace("__STAGES__", repr(stages)).replace("__SPAWN__", str(BURST["spawn"])))
        files.append(run_dir / "shape.py")
        duration = stages[-1][0]
    cmd = [sys.executable, "-m", "locust", "-f", ",".join(map(str, files)), "--headless",
           "-H", service_url(), "--csv", str(run_dir / "locust"), "--csv-full-history",
           "--html", str(run_dir / "locust_report.html"), "--logfile", str(run_dir / "locust.log"),
           "--exit-code-on-error", "0", *extra]
    env = {**os.environ, "RUN_DIR": str(run_dir)}
    with open(run_dir / "locust.stdout", "w") as out:
        proc = subprocess.run(cmd, env=env, stdout=out, stderr=subprocess.STDOUT, timeout=duration + 180)
    assert proc.returncode == 0, f"locust exited with {proc.returncode}; see {run_dir / 'locust.stdout'}"
    return cmd
 
 
# ---------------------------------------------------------------- summary
def settle_delay(series, t0, t_from):
    """Seconds from t_from until pods and HPA stay at 1 for the rest of the recording."""
    tail = [d for d in series if d["epoch"] - t0 >= t_from]
    last_bad = max(
        (
            d["epoch"] for d in tail
            if not (
                d["pods_total"] == 1
                and d["pods_ready"] == 1
                and d["hpa_current"] == 1
                and d["hpa_desired"] == 1
            )
        ),
        default=None
    )
    good = [d["epoch"] for d in tail if last_bad is None or d["epoch"] > last_bad]
    return (good[0] - t0 - t_from) if good else None
 
 
def mean_or_none(values):
    values = [v for v in values if v is not None]
    return statistics.fmean(values) if values else None
 
 
def summarize(run_dir, load, collector):
    ts, lat, ok = read_requests(run_dir)
    t0 = min(ts)
    load_end = max(t + l / 1000 for t, l in zip(ts, lat)) - t0
    n = len(lat)
    s = {
        "requests": n, "failures": n - sum(ok), "failure_rate": (n - sum(ok)) / n,
        "avg_ms": statistics.fmean(lat), "p50_ms": pct(lat, 50), "p95_ms": pct(lat, 95),
        "p99_ms": pct(lat, 99), "max_ms": max(lat), "throughput_rps": n / load_end,
        "slo_violation_rate": sum(1 for l, o in zip(lat, ok) if l > SLO_MS or not o) / n,
        "load_duration_s": load_end,
    }
    series = [dict(d, t=d["epoch"] - t0) for d in collector.rows]
    during = [d for d in series if 0 <= d["t"] <= load_end]
 
    responses = []
    for st in step_times(load):
        before = [d["hpa_desired"] for d in series if d["t"] <= st and d["hpa_desired"] is not None]
        base = before[-1] if before else 1
        hit = next((d["t"] for d in series
                    if d["t"] >= st and d["hpa_desired"] is not None and d["hpa_desired"] > base), None)
        responses.append(None if hit is None else hit - st)
    s["scale_response_s_per_step"] = responses
    s["scale_response_mean_s"] = mean_or_none(responses)
 
    currents = [d["hpa_current"] for d in series if d["hpa_current"] is not None]
    s["peak_replicas"] = max(currents) if currents else None
    s["peak_pods"] = max(d["pods_total"] for d in series)
    first_peak = next(
        (d["t"] for d in series if d["hpa_current"] == s["peak_replicas"]),
        None
    )
    # All times in series are relative to the first request timestamp.
    # This avoids negative values when a bursty run reaches its peak
    # during the initial baseline period.
    s["time_to_peak_s"] = first_peak
    s["time_to_peak_definition"] = "seconds from first request timestamp to first observed peak replica count"
 
    t_down_from = load_end if load == "normal" else last_spike_end()
    s["scale_down_s"] = settle_delay(series, t0, t_down_from)
 
    desired = [d["hpa_desired"] for d in series if d["hpa_desired"] is not None]
    diffs = [b - a for a, b in zip(desired, desired[1:]) if b != a]
    s["direction_changes"] = sum(1 for a, b in zip(diffs, diffs[1:]) if (a > 0) != (b > 0))
 
    window = [d for d in series if d["t"] >= 0]
    s["replica_minutes"] = sum(a["pods_total"] * (b["epoch"] - a["epoch"])
                               for a, b in zip(window, window[1:])) / 60
 
    util = [d["cpu_m_total"] / (d["pods_total"] * REQUEST_CPU_M) * 100 for d in during if d["pods_total"]]
    s["cpu_util_pct_mean"] = mean_or_none(util)
    s["cpu_util_pct_max"] = max(util) if util else None
    mem = [d["mem_mi_total"] / d["pods_total"] for d in during if d["pods_total"]]
    s["mem_mi_per_pod_mean"] = mean_or_none(mem)
    s["mem_mi_per_pod_max"] = max(mem) if mem else None
    inflight = [d["inflight_total"] for d in during if d["inflight_total"] is not None]
    s["inflight_mean"] = mean_or_none(inflight)
    s["inflight_max"] = max(inflight) if inflight else None
 
    startups = [r["ready"] - r["created"] for r in collector.pods_seen.values()
                if r["ready"] and r["created"] >= t0 - 5]
    s["pod_startup_mean_s"] = mean_or_none(startups)
    s["pod_startup_max_s"] = max(startups) if startups else None
 
    if load == "bursty":
        s["per_spike_peak_pods"] = [
            max((d["pods_total"] for d in series if st <= d["t"] <= st + BURST["spike_s"] + 120), default=None)
            for st in step_times(load)]
    else:
        tail = [d["pods_total"] for d in during if d["t"] >= load_end - 60]
        s["overshoot_pods"] = max(0, s["peak_pods"] - statistics.median(tail)) if tail else None
 
    (run_dir / "summary.json").write_text(json.dumps(s, indent=2))
    return s
 
 
# ---------------------------------------------------------------- the tests
CASES = [(load, run) for load in LOADS for run in range(1, RUNS + 1)]
 
 
@pytest.mark.parametrize("load,run", CASES, ids=[f"{l}-run{r}" for l, r in CASES])
def test_run(load, run):
    run_dir = RESULTS / load / f"run{run}"
    if (run_dir / "summary.json").exists():
        pytest.skip(f"{run_dir} already complete")
    shutil.rmtree(run_dir, ignore_errors=True)
    run_dir.mkdir(parents=True)
 
    print(f"\n[{SCALER} {load} run{run}] precheck")
    precheck()
    (run_dir / "deployment.yaml").write_text(sh("kubectl", "get", "deployment", DEPLOYMENT, "-o", "yaml"))
    (run_dir / "hpa.yaml").write_text(sh("kubectl", "get", "hpa", HPA_NAME, "-o", "yaml"))
 
    collector = Collector(run_dir)
    collector.start()
    try:
        time.sleep(SAMPLE_S * 3)  # baseline samples before load
        print(f"[{SCALER} {load} run{run}] load started")
        started = time.time()
        cmd = run_locust(load, run_dir)
        finished = time.time()
        print(f"[{SCALER} {load} run{run}] load finished; waiting for replicas to return to 1")
        settled = wait_until_settled(collector, SETTLE_TIMEOUT_S)
    finally:
        collector.stop()
 
    (run_dir / "events.log").write_text(sh("kubectl", "get", "events", "--sort-by=.lastTimestamp"))
    (run_dir / "hpa_describe.log").write_text(sh("kubectl", "describe", "hpa", HPA_NAME))
    meta = {
        "scaler": SCALER, "hpa_name": HPA_NAME, "load": load, "run": run, "slo_ms": SLO_MS,
        "normal": NORMAL, "bursty": BURST, "sample_interval_s": SAMPLE_S,
        "locust_started_epoch": started, "locust_finished_epoch": finished,
        "settled_epoch": settled, "locust_cmd": cmd, "expected": EXPECTED,
        "versions": {
            "locust": sh(sys.executable, "-m", "locust", "--version").strip(),
            "kubectl": sh("kubectl", "version", "--client").strip(),
            "minikube": sh("minikube", "version").strip(),
            "helm": sh("helm", "version", "--short").strip(),
        },
    }
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2))
 
    summary = summarize(run_dir, load, collector)
    print(f"[{SCALER} {load} run{run}] done: requests={summary['requests']} "
          f"p50={summary['p50_ms']:.0f}ms p95={summary['p95_ms']:.0f}ms p99={summary['p99_ms']:.0f}ms "
          f"failures={summary['failures']} peak={summary['peak_replicas']} scale_down={summary['scale_down_s']}")
    assert summary["requests"] > 0
 
 
def test_aggregate():
    rows = []
    for load in LOADS:
        runs = sorted((RESULTS / load).glob("run*/summary.json"))
        if len(runs) < RUNS:
            continue
        sums = [json.loads(p.read_text()) for p in runs]
        row = {"load": load, "runs": len(sums)}
        for key in sums[0]:
            vals = [x.get(key) for x in sums]
            if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
                mean, sd = statistics.fmean(vals), statistics.stdev(vals)
                row[f"{key}_mean"], row[f"{key}_std"] = mean, sd
                row[f"{key}_cv_pct"] = (100 * sd / mean) if mean else None
        pooled = []
        for p in runs:
            pooled += read_requests(p.parent)[1]
        for q in (50, 95, 99):
            row[f"pooled_p{q}_ms"] = pct(pooled, q)
        rows.append(row)
    if not rows:
        pytest.skip("no load has all runs complete yet")
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with open(RESULTS / "aggregate.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, restval="")
        w.writeheader()
        w.writerows(rows)
    keys = ("p50_ms", "p95_ms", "p99_ms", "throughput_rps", "failure_rate", "peak_replicas",
            "scale_response_mean_s", "time_to_peak_s", "scale_down_s", "replica_minutes")
    for r in rows:
        print(f"\n{SCALER} / {r['load']} ({r['runs']} runs): mean +- std (CV %)")
        for k in keys:
            if f"{k}_mean" in r:
                cv = r[f"{k}_cv_pct"]
                print(f"  {k:24s} {r[k + '_mean']:10.2f} +- {r[k + '_std']:8.2f}"
                      + (f"  (CV {cv:.1f}%)" if cv is not None else ""))
        print(f"  pooled p50/p95/p99 (ms): {r['pooled_p50_ms']:.0f} / {r['pooled_p95_ms']:.0f} / {r['pooled_p99_ms']:.0f}")
