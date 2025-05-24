docker build -t edge-healer:latest src/
kind load docker-image edge-healer:latest --name serf-demo
kubectl apply -f config/daemonset.yaml 
kubectl rollout restart daemonset/edge-healer -n kube-system
kubectl rollout status daemonset/edge-healer -n kube-system

# kubectl apply -f demo/busybox-spread.yaml
# kubectl rollout restart deployment busybox-spread
# kubectl rollout status deployment busybox-spread
