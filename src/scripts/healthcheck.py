import os
import sys
import urllib.request

HOST = os.environ.get("INBOX_LENS_HEALTH_HOST", "localhost")
PORT = os.environ.get("INBOX_LENS_HEALTH_PORT", "8000")
URL = f"http://{HOST}:{PORT}/health"
TIMEOUT = 5


def main() -> None:
    try:
        with urllib.request.urlopen(URL, timeout=TIMEOUT) as resp:
            code = resp.status
            resp.read()
    except Exception:
        sys.exit(1)
    sys.exit(0 if code == 200 else 1)


if __name__ == "__main__":
    main()
