#!/bin/bash
# Black-hole your API before running this inside the Kind node:
#   sudo iptables -I OUTPUT -p tcp --dport 6443 -d 10.96.0.1 -j DROP


for POD in $(crictl pods --quiet); do
  NAME=$(crictl inspectp "$POD" \
           | grep -m1 '"name":' \
           | awk -F'"' '{print $4}')
  if [[ $NAME == busybox-spread* ]]; then
    echo "→ Found busybox-spread Pod: $POD ($NAME)"
    # Get all container IDs for that Pod
    for CID in $(crictl ps --pod "$POD" --quiet); do
      echo "→ Stopping container $CID"
      crictl stop "$CID"
      echo "→ Removing container $CID"
      crictl rm   "$CID"
    done
    echo "→ All containers removed for $NAME"
    break
  fi
done


# ## get the pod start time
# POD_INFO=$(crictl inspectp $POD)
# echo "$POD_INFO" \
#   | jq -r '.status.createdAt, .status.metadata.name'


# ----
# #!/bin/bash
# # Run inside your Kind node with the API black-holed

# #!/usr/bin/env bash

# # Iterate over all Pod IDs
# for POD in $(crictl pods --quiet); do
#   # Extract the pod name
#   NAME=$(crictl inspectp "$POD" \
#            | grep -m1 '"name":' \
#            | awk -F'"' '{print $4}')

#   # Process only busybox-spread pods
#   if [[ $NAME == busybox-spread* ]]; then
#     echo "→ Found busybox-spread Pod: $POD ($NAME)"

#     # Iterate over all container IDs in this pod
#     for CID in $(crictl ps --pod "$POD" --quiet); do
#       SHORT="${CID:0:12}"  # First 12 chars for matching filenames

#       echo
#       echo "=== Container $CID ==="

#       # 1) Fetch any buffered logs via CRI
#       echo ">>> CRI logs (pre-removal):"
#       crictl logs "$CID" || echo "(no logs)"

#       # 2) Stop and remove the container
#       echo "→ Stopping container $CID"
#       crictl stop "$CID"
#       echo "→ Removing container $CID"
#       crictl rm "$CID"

#       # 3) List available log files under /var/log/containers
#       echo ">>> Available log files for $NAME under /var/log/containers:"
#       ls -1 "/var/log/containers/${NAME}_default_bb-"*.log 2>/dev/null || echo "(none found)"

#       # 4) Attempt to tail the container's on-disk log
#       logfile=$(ls "/var/log/containers/${NAME}_default_bb-${SHORT}"*.log 2>/dev/null | head -n1)
#       if [[ -n "$logfile" ]]; then
#         echo ">>> Found log file: $logfile"
#         echo ">>> File details:"
#         ls -lh "$logfile"
#         echo ">>> Inode & permissions:"
#         stat "$logfile"

#         if [[ -r "$logfile" ]]; then
#           echo ">>> Last 20 lines of $logfile:"
#           tail -n20 "$logfile"
#         else
#           echo ">>> Cannot read $logfile (permission denied?)"
#         fi

#       else
#         echo ">>> No on-disk log matching *${SHORT}*.log found, falling back..."

#         # 5) Fallback to the pod's bb container log under /var/log/pods
#         poddir=$(ls -d /var/log/pods/*"${NAME}"* 2>/dev/null | head -n1)
#         if [[ -n "$poddir" && -d "$poddir/bb" ]]; then
#           echo ">>> Contents of $poddir/bb/:"
#           ls -1 "$poddir/bb/"

#           echo ">>> Tail of $poddir/bb/0.log:"
#           cat "$poddir/bb/0.log" || echo "(failed to read $poddir/bb/0.log)"
#         else
#           echo ">>> No fallback pod log directory found under /var/log/pods"
#         fi
#       fi

#     done  # end container loop
#   fi

# done  # end pod loop

