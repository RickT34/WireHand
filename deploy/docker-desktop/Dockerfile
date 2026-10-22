FROM debian:trixie-slim
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates tini supervisor python3 python3-venv \
    xvfb x11-utils x11-xserver-utils dbus-x11 \
    xfce4-session xfce4-panel xfce4-settings xfwm4 xfdesktop4 \
    xfce4-terminal thunar mousepad firefox-esr \
    fonts-dejavu fonts-noto-cjk fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 1000 --shell /bin/bash agent \
    && mkdir -p /workspace /var/lib/kvm-mcp/recordings /run/kvm-mcp \
    && chown -R agent:agent /workspace /var/lib/kvm-mcp /run/kvm-mcp \
    && python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt /opt/kvm-requirements.txt
RUN pip install --no-cache-dir -r /opt/kvm-requirements.txt
COPY pyproject.toml README.md LICENSE /opt/kvm-mcp/
COPY src /opt/kvm-mcp/src
RUN pip install --no-cache-dir --no-deps /opt/kvm-mcp
COPY deploy/docker-desktop/*.sh deploy/docker-desktop/*.conf deploy/docker-desktop/*.py /opt/desktop/
ENV DISPLAY=:99 LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    HOME=/home/agent USER=agent LOGNAME=agent \
    XDG_RUNTIME_DIR=/run/kvm-mcp/user \
    DBUS_SESSION_BUS_ADDRESS=unix:path=/run/kvm-mcp/session-bus \
    KVM_BACKEND=docker-desktop KVM_RUNTIME=/run/kvm-mcp \
    KVM_RECORDINGS=/var/lib/kvm-mcp/recordings KVM_DESKTOP_RESOLUTION=1280x800 \
    PYTHONUNBUFFERED=1
WORKDIR /workspace
EXPOSE 8080
ENTRYPOINT ["/usr/bin/tini", "--", "/bin/bash", "/opt/desktop/entrypoint.sh"]
HEALTHCHECK --interval=15s --timeout=5s --start-period=45s --retries=3 \
    CMD ["/opt/venv/bin/python", "/opt/desktop/healthcheck.py"]
