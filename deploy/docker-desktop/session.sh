#!/bin/bash
set -euo pipefail
for attempt in {1..100}; do
    if xdpyinfo >/dev/null 2>&1; then
        xset -dpms s off s noblank || true
        setxkbmap us
        exec xfce4-session
    fi
    sleep .1
done
echo 'Xvfb did not become ready' >&2
exit 1
