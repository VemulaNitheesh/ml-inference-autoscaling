# Environment baseline - 2026-11-24T20:50:57+05:30
## OS
Description:	Ubuntu 24.04.4 LTS
## Kernel
7.0.0-28-generic
## CPU
8
Model name:                              11th Gen Intel(R) Core(TM) i5-1155G7 @ 2.50GHz
## RAM
               total        used        free      shared  buff/cache   available
Mem:            15Gi       3.8Gi       8.2Gi       708Mi       4.0Gi        11Gi
Swap:          4.0Gi          0B       4.0Gi
## Disk
Filesystem      Size  Used Avail Use% Mounted on
/dev/nvme0n1p5  147G   36G  104G  26% /
## Python
Python 3.12.3
## Git
git version 2.43.0
## Docker
Docker version 29.1.3, build 29.1.3-0ubuntu3~24.04.2
## kubectl
Client Version: v1.35.10
## Minikube
minikube version: v1.39.0
## Helm
v3.22.1+gd6c1a0e

## Pinned versions (decided at Milestone 1)
- Minikube: v1.39.0
- Kubernetes (cluster): v1.35.10 (matches kubectl client v1.35.10)
- Container runtime: containerd (pinned explicitly, as the Minikube default changed in v1.39.0)
- Minikube driver: docker
- Minikube resources: 4 CPUs, 6144 MB RAM, 30 GB disk
- Host power profile during experiments: performance
- Jenkins stopped during experiments

## Cluster components (observed at cluster creation)
- containerd: 2.3.4
- Minikube base image (kicbase): v0.0.51
- Metrics Server: v0.9.0 (minikube addon)
- Cluster created: 2026-11-24
