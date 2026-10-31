#!/usr/bin/env python3
"""Static check of the hosted-app mediated-egress Terraform (red-team N-2, networking.md §5.5).

The counterpart to profiler_sandbox_check.py: the profiler network must have NO way out; the
app-egress network must have a mediated way out — allow 443/80, deny the rest — so that ordinary
HTTPS API calls work but raw TCP (stratum mining, port-25 spam, C2 on arbitrary ports) is blocked.

Asserts, over infra/terraform/modules/app_egress:
  1. There is exactly one deny-all EGRESS firewall rule, and the only EGRESS allow rules open
     tcp:443/80 to the internet (plus the metadata server). No allow rule opens `all`, `udp` to
     the internet, or a wider TCP port set.
  2. The deny-all rule's priority is a larger number (lower precedence) than every allow rule's,
     so the allows win and everything else is denied.
  3. Cloud NAT exists (the subnet has no external IPs, so without it even 443 can't leave) and uses
     a reserved IP (MANUAL_ONLY), which the console shows for customer-DB allow-listing.

Run: python3 infra/tests/app_egress_check.py [infra/terraform/modules/app_egress]
CI's infra job runs it, alongside iam_guard.py.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_BLOCK = re.compile(r'^resource\s+"([^"]+)"\s+"([^"]+)"\s*\{', re.M)
_ALLOWED_INTERNET_TCP_PORTS = {"80", "443"}
_INTERNET = ("0.0.0.0/0",)
_METADATA = ("169.254.169.254/32",)


def _resource_blocks(text: str):
    """Yield (type, name, body) for each top-level resource block."""
    for m in _BLOCK.finditer(text):
        start = m.end()
        depth, i = 1, start
        while i < len(text) and depth:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        yield m.group(1), m.group(2), text[start:i - 1]


def _attr(body: str, name: str) -> str | None:
    m = re.search(rf'\b{name}\s*=\s*(?:"([^"]*)"|(\d+))', body)
    if not m:
        return None
    return m.group(1) if m.group(1) is not None else m.group(2)


def _var_default(text: str, var_name: str) -> list[str] | None:
    """The `default = [...]` of `variable "var_name"`, so a `ports = var.foo` reference resolves."""
    m = re.search(rf'variable\s+"{var_name}"\s*\{{', text)
    if not m:
        return None
    depth, i = 1, m.end()
    while i < len(text) and depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    d = re.search(r'\bdefault\s*=\s*\[([^\]]*)\]', text[m.end():i])
    return re.findall(r'"([^"]+)"', d.group(1)) if d else None


def _list(body: str, name: str, text: str = "") -> list[str] | None:
    m = re.search(rf'\b{name}\s*=\s*\[([^\]]*)\]', body)
    if m:
        return re.findall(r'"([^"]+)"', m.group(1))
    ref = re.search(rf'\b{name}\s*=\s*var\.(\w+)', body)
    if ref and text:
        return _var_default(text, ref.group(1))
    return None


def check(root: Path) -> list[str]:
    errs: list[str] = []
    files = sorted(root.glob("*.tf"))
    if not files:
        return [f"no .tf files under {root}"]
    text = "\n".join(f.read_text() for f in files)

    firewalls = [(n, b) for t, n, b in _resource_blocks(text) if t == "google_compute_firewall"]
    egress = [(n, b) for n, b in firewalls if (_attr(b, "direction") or "").upper() == "EGRESS"]
    if not egress:
        return ["no EGRESS firewall rules found in the app-egress module"]

    deny = [(n, b) for n, b in egress if re.search(r'\bdeny\s*\{', b)]
    allow = [(n, b) for n, b in egress if re.search(r'\ballow\s*\{', b)]

    if len(deny) != 1:
        errs.append(f"expected exactly one deny EGRESS rule, found {len(deny)}: {[n for n, _ in deny]}")
    deny_prio = min((int(_attr(b, "priority") or "1000") for _, b in deny), default=1000)

    for name, body in deny:
        if list(_list(body, "destination_ranges") or []) != list(_INTERNET):
            errs.append(f"deny rule {name} must deny to {_INTERNET}, got {_list(body, 'destination_ranges')}")
        if not re.search(r'deny\s*\{[^}]*protocol\s*=\s*"all"', body, re.S):
            errs.append(f"deny rule {name} must deny protocol \"all\"")

    for name, body in allow:
        prio = int(_attr(body, "priority") or "1000")
        if prio >= deny_prio:
            errs.append(f"allow rule {name} (priority {prio}) must out-prioritise the deny rule (priority {deny_prio})")
        dests = _list(body, "destination_ranges") or []
        # An allow rule may only open the internet to tcp:443/80, or reach the metadata server.
        if list(dests) == list(_INTERNET):
            blocks = re.findall(r'allow\s*\{(.*?)\}', body, re.S)
            for blk in blocks:
                proto = _attr(blk, "protocol")
                if proto != "tcp":
                    errs.append(f"allow rule {name} opens protocol {proto!r} to the internet; only tcp:443/80 is allowed")
                    continue
                ports = set(_list(blk, "ports", text) or [])
                extra = ports - _ALLOWED_INTERNET_TCP_PORTS
                if not ports or extra:
                    errs.append(f"allow rule {name} opens tcp ports {sorted(ports)} to the internet; only {sorted(_ALLOWED_INTERNET_TCP_PORTS)} are allowed")
        elif list(dests) != list(_METADATA):
            errs.append(f"allow rule {name} opens an unexpected destination {dests}; only the internet (tcp 443/80) or the metadata server may be allowed")

    nats = [(n, b) for t, n, b in _resource_blocks(text) if t == "google_compute_router_nat"]
    if not nats:
        errs.append("no Cloud NAT (google_compute_router_nat): the subnet has no external IPs, so even allowed 443 traffic can't leave")
    for name, body in nats:
        if _attr(body, "nat_ip_allocate_option") != "MANUAL_ONLY":
            errs.append(f"Cloud NAT {name} should use a reserved IP (MANUAL_ONLY) so the console can show a stable allow-list address")

    return errs


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "terraform" / "modules" / "app_egress"
    errs = check(root)
    if errs:
        print("app-egress check FAILED:", file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print(f"ok: app-egress allows only HTTPS/HTTP out, denies the rest, and has a NAT with a reserved IP ({root})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
