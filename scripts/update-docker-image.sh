docker build -t edge-healer:latest ../src/
kind load docker-image edge-healer:latest --name serf-demo
kubectl rollout restart daemonset/edge-healer -n kube-system
kubectl rollout status daemonset/edge-healer -n kube-system