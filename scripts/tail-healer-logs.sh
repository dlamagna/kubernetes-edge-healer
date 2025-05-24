POD=$(kubectl get pod -n kube-system -l app=edge-healer \
      -o jsonpath='{.items[0].metadata.name}')

kubectl logs -n kube-system $POD -c healer --follow