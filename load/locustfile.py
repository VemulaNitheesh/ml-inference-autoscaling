import json
from pathlib import Path

from locust import HttpUser, task, constant


PAYLOAD = json.loads(
    (Path(__file__).parent / "payload.json").read_text()
)


class InferenceUser(HttpUser):
    wait_time = constant(0)

    @task
    def predict(self):
        self.client.post(
            "/predict",
            json=PAYLOAD,
            headers={"Connection": "close"},
            name="/predict"
        )
