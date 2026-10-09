import os
import ssl
import subprocess
import sys
import urllib.request
from pathlib import Path

if len(sys.argv) > 1 and sys.argv[1] == "quickstart":
    status = subprocess.check_output(
        ["supervisorctl", "-c", "/app/deploy/supervisord.conf", "status"], text=True
    )
    for name in ("api", "knowledge", "web"):
        if not any(
            line.split()[:2] == [name, "RUNNING"] for line in status.splitlines()
        ):
            raise SystemExit(1)
    # Quickstart serves a self-signed certificate, so verification is disabled. This
    # only asserts that nginx and the API answer on the loopback interface.
    url = "https://127.0.0.1:8080/api/v1/ready"
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    # The entry point no longer injects an identity, and the probe's environment
    # does not inherit the token bootstrap exports, so authenticate with the
    # break-glass credential bootstrap wrote before supervisord started.
    token = Path("/data/credentials/local-access.token").read_text(encoding="utf-8").strip()
else:
    url = "http://127.0.0.1:8000/api/v1/ready"
    context = None
    token = os.environ.get("AGENT_API_TOKEN")
request = urllib.request.Request(url)
if token:
    request.add_header("Authorization", "Bearer " + token)
with urllib.request.urlopen(request, timeout=5, context=context) as response:
    if response.status != 200:
        raise SystemExit(1)
