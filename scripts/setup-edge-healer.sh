#!/usr/bin/env bash
# setup-edge-healer.sh
# Fully configures a KinD cluster named "serf-demo" for edge-healer.

set -euo pipefail

CLUSTER_NAME="serf-demo"
IMAGE_NAME="edge-healer:0.1"
KIND_CONFIG="
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
  - role: worker
  - role: worker
  - role: worker
"

echo "1️⃣  Creating or reusing KinD cluster '${CLUSTER_NAME}'..."
if kind get clusters | grep -q "^${CLUSTER_NAME}\$"; then
  echo "   → cluster '${CLUSTER_NAME}' already exists, skipping creation."
else
  echo "${KIND_CONFIG}" | kind create cluster --name "${CLUSTER_NAME}" --config=-
fi

echo
echo "2️⃣  Building Docker image '${IMAGE_NAME}'..."
docker build -t "${IMAGE_NAME}" .

echo
echo "3️⃣  Loading image into KinD cluster..."
kind load docker-image "${IMAGE_NAME}" --name "${CLUSTER_NAME}"

echo
echo "4️⃣  Applying RBAC for edge-healer..."
kubectl apply -f config/rbac.yaml

echo
echo "5️⃣  Deploying edge-healer DaemonSet..."
kubectl apply -f config/daemonset.yaml

echo
echo "6️⃣  (Optional) Exposing metrics via NodePort..."
kubectl apply -f config/service-metrics.yaml

echo
echo "7️⃣  Waiting for edge-healer rollout to complete..."
kubectl -n kube-system rollout status ds/edge-healer

echo
echo "8️⃣  Deploying busybox-spread test workload..."
kubectl apply -f dev/busybox-spread.yaml

echo
echo "✅  Setup complete!"
echo "→ To test healing, delete one of the busybox-spread pods:"
echo "    kubectl delete pod \$(kubectl get pod -l app=busybox-spread -o name | head -1) --grace-period=0 --force"
echo "→ To view logs:"
echo "    kubectl -n kube-system logs ds/edge-healer -c healer --follow"
echo "→ To fetch metrics (node host IP from 'kubectl get nodes -o wide'):"
echo "    curl http://<NODE_IP>:8000/metrics | grep restore_latency"



