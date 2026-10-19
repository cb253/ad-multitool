"""Non-UI networking helpers used by the Networking tab."""

import re
import socket
import time

# Hostname, IPv4, or IPv6 (with optional %zone). Must not start with "-" so the
# target can never be read as a command-line flag by ping/tracert/nslookup.
NETWORK_TARGET_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-:_%]*$")
PORT_TEST_TIMEOUT_SECONDS = 3


def is_valid_target(target):
    return bool(NETWORK_TARGET_PATTERN.match(target))


def check_tcp_port(host, port):
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=PORT_TEST_TIMEOUT_SECONDS):
            elapsed_ms = (time.monotonic() - start) * 1000
            return f"TCP {host}:{port} is OPEN ({elapsed_ms:.0f} ms)"
    except socket.timeout:
        return f"TCP {host}:{port} timed out after {PORT_TEST_TIMEOUT_SECONDS}s (filtered or host down)"
    except OSError as exc:
        return f"TCP {host}:{port} is CLOSED or unreachable ({exc})"
