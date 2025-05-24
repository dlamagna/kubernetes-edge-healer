# (a) find your API-server’s IP (from inside the node):
APISRV=$(kubectl get endpoints kubernetes \
           --namespace default \
           -o jsonpath='{.subsets[0].addresses[0].ip}')

# (b) drop all TCP to port 6443 (kube-api):
sudo iptables -I OUTPUT -d $APISRV -p tcp --dport 6443 -j DROP

# check if it realyl is out
kubectl get nodes --request-timeout=3s


# bring it back
# sudo iptables -D OUTPUT -d $APISRV -p tcp --dport 6443 -j DROP
