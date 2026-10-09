import os
import ssl
import subprocess
import sys
import urllib.request

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
else:
    url = "http://127.0.0.1:8000/api/v1/ready"
    context = None
request = urllib.request.Request(url)
if len(sys.argv) == 1 and os.environ.get("AGENT_API_TOKEN"):
    request.add_header("Authorization", "Bearer " + os.environ["AGENT_API_TOKEN"])
with urllib.request.urlopen(request, timeout=5, context=context) as response:
    if response.status != 200:
        raise SystemExit(1)
