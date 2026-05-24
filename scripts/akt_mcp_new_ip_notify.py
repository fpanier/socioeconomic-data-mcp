#!/usr/bin/env python3
"""Email an alert when a NEW client IP first *uses* the MCP connector.

Open-mode "signup" proxy: with no login there's no registration event, so we watch the
nginx access log for IPs that make a successful MCP call (POST /mcp, 200/202), track which
we've already seen, and email the new ones via socioeconomic_data_mcp.notify (Brevo, reusing the
configured key). Designed to run from cron every few minutes.

First run seeds the seen-set silently (no emails) so deploy doesn't flood the inbox.

Env (with defaults):
  MCP_ACCESS_LOG  /var/log/nginx/akt-mcp.access.log
  MCP_SEEN_IPS    /var/lib/akt-data-mcp/seen_ips.txt
  MCP_ENV_FILE    /etc/akt-data-mcp.env   (loaded so BREVO_API_KEY/MCP_ALERT_EMAIL are available)
plus the email vars used by socioeconomic_data_mcp.notify.
"""

from __future__ import annotations

import os
import re
import sys

_LINE = re.compile(r'^(\S+) \S+ \S+ \[[^\]]+\] "(\S+) (\S+)[^"]*" (\d{3})')


def successful_mcp_ips(lines) -> set[str]:
    """IPs that made a successful MCP call (POST /mcp, status 200/202)."""
    ips: set[str] = set()
    for line in lines:
        m = _LINE.match(line)
        if not m:
            continue
        ip, method, path, status = m.groups()
        if method == "POST" and path.startswith("/mcp") and status in ("200", "202"):
            ips.add(ip)
    return ips


def _load_env(path: str) -> None:
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
    except OSError:
        pass


def main() -> int:
    log_path = os.environ.get("MCP_ACCESS_LOG", "/var/log/nginx/akt-mcp.access.log")
    seen_path = os.environ.get("MCP_SEEN_IPS", "/var/lib/akt-data-mcp/seen_ips.txt")
    _load_env(os.environ.get("MCP_ENV_FILE", "/etc/akt-data-mcp.env"))

    try:
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            ips = successful_mcp_ips(fh)
    except OSError:
        return 0  # no log yet

    first_run = not os.path.exists(seen_path)
    seen: set[str] = set()
    if not first_run:
        with open(seen_path, encoding="utf-8") as fh:
            seen = {ln.strip() for ln in fh if ln.strip()}

    new = sorted(ips - seen)
    os.makedirs(os.path.dirname(seen_path), exist_ok=True)
    with open(seen_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(sorted(ips | seen)) + "\n")

    if first_run or not new:
        return 0  # seed silently, or nothing new

    # import the package's notifier (installed in the venv this runs under)
    try:
        from socioeconomic_data_mcp import notify
    except ModuleNotFoundError:
        sys.path.insert(0, "/opt/akt-data-mcp/src")
        from socioeconomic_data_mcp import notify

    for ip in new:
        notify._send(
            "New user — Socio-Economic Data MCP connector",
            f"A new client IP just used the connector (successful MCP call).\n\nIP: {ip}\n"
            "(open access, no login — detected from the access log)\n",
        )
    return 0


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(main())
