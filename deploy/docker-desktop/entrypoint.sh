#!/bin/bash
set -euo pipefail
: "${KVM_TOKEN:?KVM_TOKEN must be set}"
if (( ${#KVM_TOKEN} < 20 )); then
    echo 'KVM_TOKEN must contain at least 20 characters' >&2
    exit 1
fi
if [[ ! "$KVM_DESKTOP_RESOLUTION" =~ ^([0-9]{3,4})x([0-9]{3,4})$ ]] \
    || (( 10#${BASH_REMATCH[1]} < 640 || 10#${BASH_REMATCH[1]} > 3840 \
       || 10#${BASH_REMATCH[2]} < 480 || 10#${BASH_REMATCH[2]} > 2160 )); then
    echo 'KVM_DESKTOP_RESOLUTION must be WIDTHxHEIGHT, 640..3840 by 480..2160' >&2
    exit 1
fi
mkdir -p /run/kvm-mcp/user /var/lib/kvm-mcp/recordings /workspace /home/agent /tmp/.X11-unix /tmp/.ICE-unix
chown root:root /tmp/.X11-unix /tmp/.ICE-unix
chmod 1777 /tmp/.X11-unix /tmp/.ICE-unix
chmod 700 /run/kvm-mcp/user
chown agent:agent /run/kvm-mcp /run/kvm-mcp/user /var/lib/kvm-mcp /var/lib/kvm-mcp/recordings /workspace /home/agent
# Seed only a fresh home; never overwrite desktop settings on restart/rebuild.
if [ ! -e /home/agent/.kvm-desktop-initialized ]; then
    cp -an /etc/skel/. /home/agent/
    mkdir -p /home/agent/Desktop /home/agent/Documents /home/agent/Downloads
    if [ ! -e /home/agent/Workspace ] && [ ! -L /home/agent/Workspace ]; then
        ln -s /workspace /home/agent/Workspace
    fi
    touch /home/agent/.kvm-desktop-initialized
    chown -R agent:agent /home/agent
fi
rm -f /run/kvm-mcp/session-bus /tmp/.X99-lock /tmp/.X11-unix/X99
exec supervisord -n -c /opt/desktop/supervisord.conf
