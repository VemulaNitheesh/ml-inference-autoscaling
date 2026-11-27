import csv
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
