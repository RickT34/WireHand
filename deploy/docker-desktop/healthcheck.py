"""Check the local HTTP server and actual X11 capture without exposing a token."""
from pathlib import Path
import urllib.request

from kvm.platforms.docker_desktop.backend import DockerDesktopBackend

urllib.request.urlopen('http://127.0.0.1:8080/', timeout=2).close()
DockerDesktopBackend(Path('/run/kvm-mcp')).screenshot(320)
