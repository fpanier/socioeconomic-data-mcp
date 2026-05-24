#!/usr/bin/env python3
"""Daily usage + IP report for the AKT Data MCP connector.

Parses the dedicated nginx access log for api.fep-consult.be and summarises a day:
total requests, unique client IPs, top IPs, status-code breakdown, hourly histogram,
and MCP tool-call count (POST /mcp).

Usage:
    akt-mcp-report                 # today (UTC)
    akt-mcp-report 2026-05-24      # a specific date
    akt-mcp-report 2026-05-24 /path/to/access.log
"""

from __future__ import annotations

import datetime as dt
import re
import sys
from collections import Counter

LOG_DEFAULT = "/var/log/nginx/akt-mcp.access.log"
# combined: IP - - [24/May/2026:13:00:00 +0000] "POST /mcp HTTP/1.1" 200 1234 "ref" "ua"
LINE = re.compile(r'^(\S+) \S+ \S+ \[([^:]+):(\d\d):\d\d:\d\d [^\]]*\] "(\S+) (\S+)[^"]*" (\d{3})')


def main() -> int:
    date = sys.argv[1] if len(sys.argv) > 1 else dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    path = sys.argv[2] if len(sys.argv) > 2 else LOG_DEFAULT
    try:
        day_tag = dt.datetime.strptime(date, "%Y-%m-%d").strftime("%d/%b/%Y")
    except ValueError:
        print(f"bad date {date!r}; use YYYY-MM-DD")
        return 2

    total = mcp_calls = 0
    ips: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    hours: Counter[str] = Counter()
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"cannot read {path}: {e}")
        return 1
    with fh:
        for line in fh:
            m = LINE.match(line)
            if not m or m.group(2) != day_tag:
                continue
            ip, _, hour, method, urlpath, status = m.groups()
            total += 1
            ips[ip] += 1
            statuses[status] += 1
            hours[hour] += 1
            if method == "POST" and urlpath.startswith("/mcp"):
                mcp_calls += 1

    print(f"=== AKT Data MCP — usage report for {date} (UTC) ===")
    print(f"log: {path}")
    print(f"total requests : {total}")
    print(f"MCP tool calls : {mcp_calls}  (POST /mcp)")
    print(f"unique IPs     : {len(ips)}")
    if not total:
        print("(no requests logged for this date)")
        return 0
    print(f"status codes   : " + ", ".join(f"{s}:{n}" for s, n in sorted(statuses.items())))
    rl = statuses.get("429", 0)
    if rl:
        print(f"rate-limited   : {rl} (429)")
    print("top IPs:")
    for ip, n in ips.most_common(20):
        print(f"  {n:6d}  {ip}")
    print("by hour (UTC):")
    for h in sorted(hours):
        print(f"  {h}:00  {'#' * min(hours[h], 60)} {hours[h]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
