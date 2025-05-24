RS=$(kubectl get rs -n default -l app=busybox-spread -o jsonpath='{.items[0].metadata.name}')
kubectl delete rs "$RS" -n default

kubectl logs -n kube-system -l app=edge-healer -c healer --follow # | grep --line-buffered "edge-healer.cache.*save_rs"

