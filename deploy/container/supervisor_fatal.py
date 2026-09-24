"""Stop the supervisor if a required child exhausts its restart attempts."""

import os
import signal
import sys

while True:
    print("READY", flush=True)
    header = sys.stdin.readline()
    if not header:
        break
    fields = dict(part.split(":", 1) for part in header.split())
    sys.stdin.read(int(fields["len"]))
    print("RESULT 2\nOK", end="", flush=True)
    os.kill(os.getppid(), signal.SIGTERM)
