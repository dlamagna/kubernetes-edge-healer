FROM python:3.11-slim

# install OS tools + crictl
ARG CRICTL_VER=v1.29.0
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      vim \
      curl \
      iproute2 \
      iptables \
 && curl -Lo crictl-${CRICTL_VER}-linux-amd64.tar.gz \
       https://github.com/kubernetes-sigs/cri-tools/releases/download/${CRICTL_VER}/crictl-${CRICTL_VER}-linux-amd64.tar.gz \
 && tar -C /usr/local/bin -xzf crictl-${CRICTL_VER}-linux-amd64.tar.gz \
 && rm crictl-${CRICTL_VER}-linux-amd64.tar.gz \
 && rm -rf /var/lib/apt/lists/*

# copy & install Python deps
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# copy operator sources
COPY . .

# run Kopf
ENTRYPOINT ["python", "-m", "kopf", "run", "main.py"]
