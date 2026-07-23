FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    curl \
    ca-certificates \
    git \
    build-essential \
    gcc \
    python3 \
    python3-venv \
    python3-dev \
    python3-pip \
    libmagic1 \
    screen \
    debian-keyring \
    debian-archive-keyring \
    apt-transport-https \
    gnupg \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Install Caddy
RUN curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg \
    && curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list \
    && apt-get update && apt-get install -y caddy \
    && rm -rf /var/lib/apt/lists/*

# Install GraphSpy
RUN pip3 install --break-system-packages graphspy

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /app

COPY . .

RUN chmod +x deploy.sh start.sh entrypoint.sh

RUN ./deploy.sh

ENV ADMIN_HOST=0.0.0.0
ENV PHISHING_HOST=0.0.0.0

EXPOSE 1234 8000

ENTRYPOINT ["./entrypoint.sh"]
