# Edge-Healer

A lightweight self-healing DaemonSet agent for Kubernetes edge clusters.  
Whenever the control-plane (API server) becomes unavailable, the agent gossips liveness & free-capacity among peers, caches desired ReplicaSet specs locally, and optimistically re-binds missing Pods on the healthiest survivor node. When the WAN link returns, it reconciles duplicates to prevent split-brain.

---

## Features

- **Serf-based gossip** (via python-serfclient) for peer health and free-CPU exchanges  
- **Kopf operator** (async) to watch Pods & ReplicaSets  
- **Optimistic patch /binding** to restore deleted Pods in ≤ 0.3 s  
- **SQLite cache** of desired ReplicaSet state for cold-boot offline recovery  
- **Prometheus metrics** (restore latency histogram, bind conflicts, gossip updates)  

---

## Architecture

### Node-Level Architecture

```mermaid
flowchart TB
    subgraph Control_Plane["Control Plane (API Server)"]
        CP["API Server"]
    end

    subgraph Node1["Node 1"]
        subgraph Pod1["Edge-Healer Pod"]
            direction TB
            C1["Edge-Healer Container (Python)"]
            S1["Serf Sidecar (Go binary)"]
        end
    end

    subgraph Node2["Node 2"]
        subgraph Pod2["Edge-Healer Pod"]
            direction TB
            C2["Edge-Healer Container (Python)"]
            S2["Serf Sidecar (Go binary)"]
        end
    end

    subgraph Node3["Node 3"]
        subgraph Pod3["Edge-Healer Pod"]
            direction TB
            C3["Edge-Healer Container (Python)"]
            S3["Serf Sidecar (Go binary)"]
        end
    end

    %% Control Plane connections (dashed to show it can be cut off)
    CP -.->|"API Calls (can be blocked)"| Pod1
    CP -.->|"API Calls (can be blocked)"| Pod2
    CP -.->|"API Calls (can be blocked)"| Pod3

    %% Serf gossip network (solid lines to show it's always active)
    S1 ---|"Gossip Protocol"| S2
    S2 ---|"Gossip Protocol"| S3
    S3 ---|"Gossip Protocol"| S1

    %% Layout hints
    classDef node fill:#326ce5,stroke:#333,stroke-width:2px
    classDef pod stroke:#333,stroke-width:1px
    classDef control stroke:#333
    class Node1,Node2,Node3 node
    class Pod1,Pod2,Pod3 pod
    class Control_Plane control
```

### System Flow

```mermaid
flowchart TD
  subgraph Node_Setup
    A1["Deploy Edge-Healer DaemonSet"] --> A2["Each node gets one Pod with:"]
    A2 --> A3["1. Edge-Healer Container (Python)"]
    A2 --> A4["2. Serf Sidecar Container (Go binary)"]
    A3 --> A5["Monitors local pods & handles bidding"]
    A4 --> A6["Handles node-to-node gossip"]
  end

  subgraph Gossip_Network
    direction LR
    G1["Serf Sidecars"] -->|gossip protocol| G2["Share CPU availability"]
    G2 -->|update| G1
  end

  subgraph Pod_Recovery
    direction TB
    B1["Control Plane Offline"] --> B2["Local pod deletion detected"]
    B2 --> B3["Edge-Healer Container:"]
    B3 --> B4["1. Check CPU availability via Serf"]
    B4 --> B5["2. Run bidding algorithm"]
    B5 --> B6["3. Bind pod if won bid"]
  end

  subgraph Recovery_Metrics
    direction TB
    B6 --> C1["Control Plane Online"]
    C1 --> C2["Reconcile duplicates"]
    C2 --> C3["Record metrics:"]
    C3 --> M1["Restore latency"]
    C3 --> M2["Bind conflicts"]
    C3 --> M3["Gossip updates"]
  end

  A6 --> G1
  G2 --> B4
  B6 --> C1
```

## Repository Layout

```
.
├── src/
│   ├── cache.py                  # SQLite-based DesiredStateCache
│   ├── gossip.py                 # SerfGossip wrapper
│   ├── main.py                   # Kopf operator entrypoint
│   ├── metrics.py                # Prometheus metrics & HTTP server
│   ├── scheduler.py              # bid_and_bind logic
│   └── requirements.txt
├── config/
│   ├── daemonset.yaml           # DaemonSet + Serf sidecar
│   └── service-metrics.yaml     # (optional) NodePort Service for metrics
├── demo/
│   └── busybox-spread.yaml      # test workload with one replica/node
└── README.md
```

---

## Prerequisites

- Kubernetes cluster (e.g. [KinD][kind] named `serf-demo`)  
- `docker`, `kubectl`, `kind` CLIs installed  
- Python 3.11+ (for local development)  

[kind]: https://kind.sigs.k8s.io/

- demo: legacy iptables
    ```bash
    sudo update-alternatives --set iptables   /usr/sbin/iptables-legacy
    sudo update-alternatives --set ip6tables  /usr/sbin/ip6tables-legacy
    ```

---

## Build & Load Image

```bash
# From repo root
docker build -t edge-healer:latest .

# For KinD cluster "serf-demo"
kind load docker-image edge-healer:latest --name serf-demo
```

---

## Deploy the DaemonSet

1. Apply the DaemonSet YAML (includes Serf sidecar):

   ```bash
   kubectl apply -f config/daemonset.yaml
   kubectl -n kube-system rollout status ds/edge-healer
   ```

   This will deploy:
   - One edge-healer pod per node
   - Each pod contains:
     - Edge-healer container (Python application)
     - Serf sidecar container (gossip protocol)

---

## Verify & Test Healing

1. Deploy test workload (one Pod per node):

   ```bash
   # this is the demo purposed pod
   kubectl apply -f demo/busybox-spread.yaml
   ```

2. Force-delete a Pod (choose one method):

   Method A: Online Deletion (when control plane is reachable)
   ```bash
   POD=$(kubectl get pod -l app=busybox-spread -o name | head -1)
   kubectl delete "$POD" --grace-period=0 --force
   ```

   Method B: Offline Deletion (when control plane is unreachable)
   ```bash
   # First, block API server access
   sudo iptables -A OUTPUT -p tcp --dport 6443 -j DROP
   
   # Copy the internal deletion script into the worker container and execute it
   # For example with node name serf-demo-worker2
   docker cp scripts/internal-pod-deletion.sh serf-demo-worker2:internal-pod-deletion.sh
   docker exec serf-demo-worker2 chmod +x internal-pod-deletion.sh
   docker exec serf-demo-worker2 bash -c './internal-pod-deletion.sh'
   
   # After testing, restore API access
   sudo iptables -D OUTPUT -p tcp --dport 6443 -j DROP
   ```

3. Watch healer logs for the bind decision:

   ```bash
   kubectl -n kube-system logs ds/edge-healer -c healer --since=5s | grep "won bid"
   ```

4. Confirm replacement Pod appears in < 0.3 s.

---

## Metrics & Monitoring

By default the agent exposes Prometheus metrics on port 8000 of each node (via `hostNetwork: true`). Key metrics:

* `restore_latency_seconds` - Time taken to restore a pod
* `bind_conflicts_total` - Number of failed binding attempts
* `peer_updates_total` - Number of CPU availability updates received

### Direct curl

```bash
NODE_IP=$(kubectl get node -o wide | awk 'NR==2{print $6}')
curl -s http://$NODE_IP:8000/metrics \
  | grep -E 'restore_latency|peer_updates'
```

### (Optional) NodePort Service

```bash
kubectl apply -f config/service-metrics.yaml
curl -s http://<NODE_IP>:30080/metrics \
  | grep -E 'restore_latency|peer_updates'
```

---

## Running Tests

Unit and integration tests are provided in the `src/tests/` directory. To run the tests locally:

1. Create and activate a virtual environment (if not already):

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

2. Install the package in development mode with test dependencies:

   ```bash
   pip install -e ".[test]"
   ```

3. Run all tests (from the project root directory):

   ```bash
   # Make sure you're in the project root directory (where setup.py is located)
   pytest src/tests/
   ```

4. Run with coverage:

   ```bash
   pytest --cov=src src/tests/
   ```

5. Run specific test categories:

   ```bash
   pytest src/tests/unit/         # Run only unit tests
   pytest src/tests/integration/  # Run only integration tests
   ```

Tests use `pytest`, `pytest-asyncio`, and `pytest-mock` for mocking and async support. Fixtures and mocks are provided in `src/tests/conftest.py`.

---

## Advanced Topics and Future work

* **Peer-TTL pruning**: drop Serf peers idle > 2 s
* **Cold-boot replay**: restore missing Pods on startup if offline
* **Split-brain reconciliation**: delete duplicates when control-plane returns
* **uvloop integration**: add `uvloop.install()` in `main.py` for lower latency
* **Unit tests**: use `pytest-asyncio` and mocks for bidding logic
* **Packaging**: Helm chart or Kustomize overlay

