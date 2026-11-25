# Experiment 1: Queue-Based HPA

## Configuration
- Application: RandomForestClassifier inference API
- Infrastructure: Minikube
- Autoscaler: Kubernetes HPA
- Custom metric: inference_queue_depth
- Target average queue depth: 5
- Minimum replicas: 1
- Maximum replicas: 5
- Maximum concurrent inferences per Pod: 2
- Load generator: Locust

## Observations
- Initial replicas: 1
- Maximum observed replicas: 3
- Final replicas: 1
- Observed queue metric during load: 14
- Subsequent observed queue metric: 4
- Queue after load stopped: 0
- Scale-up: Successful
- Scale-down: Successful
- Deployment returned to minimum replicas.

## Additional observations
- Original Pod initially accumulated most waiting requests.
- Newly created Pods initially reported zero queue depth.
- Queue depth fluctuated during sustained load.
- HPA successfully retrieved the custom metric.

## Limitations
- Exact Pod startup latency not yet measured.
- Complete request latency distribution not archived.
- Detailed timestamped experiment logs not yet collected.

## Scale-down observations
- HPA observed queue metric: 0/5.
- Replica transition: 3 -> 2 -> 1.
- Final Deployment readiness: 1/1.
- Deployment returned to minimum replicas automatically.

## Load test observations
- Locust initially reported 1,877 requests.
- Initial single-user test reported 24.7 requests/second.
- Initial single-user test reported 0% failures.
- Detailed statistics for the subsequent 20-user test were not archived.
