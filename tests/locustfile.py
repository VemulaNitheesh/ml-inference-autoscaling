from locust import HttpUser, task, between
import json

with open("payload.json") as f:
    PAYLOAD = json.load(f)


class InferenceUser(HttpUser):
    wait_time = between(0.01, 0.05)

    @task
    def predict(self):
        self.client.post(
            "/predict",
            json=PAYLOAD,
            name="/predict",
            headers={"Connection": "close"}
        )
