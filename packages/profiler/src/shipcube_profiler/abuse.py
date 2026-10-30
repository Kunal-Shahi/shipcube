"""Abuse signals from a measured profile (docs/research/profiler-sandbox.md §8).

Computed on the control-plane side from the verified result; the control plane turns
them into strikes and suspensions. A memory kill is not a signal: it's a normal outcome
for memory-heavy apps.
"""

from __future__ import annotations

from .profile import Profile

EGRESS_FLOOD = 50
MINING_PORTS = {3333, 4444, 5555, 7777, 14444, 45700}
MINING_HOST_HINTS = ("pool.", "xmr", "monero", "nicehash", "nanopool", "2miners", "f2pool", "ethermine",
                     "minexmr", "supportxmr", "hashvault", "stratum")


def abuse_signals(p: Profile) -> list[str]:
    if p.mode != "measured":
        return ["result_forged"] if p.static_reason == "result_rejected" else []
    out = []
    if p.net_blocked > EGRESS_FLOOD:
        out.append("egress_flood")
    hosts = [h.lower() for h in p.remote_hosts]
    if MINING_PORTS & set(p.remote_ports) or any(k in h for h in hosts for k in MINING_HOST_HINTS):
        out.append("mining_pool")
    reason = p.killed_reason or ""
    if "processes" in reason:
        out.append("fork_bomb")
    if "disk" in reason:
        out.append("disk_flood")
    if p.tamper_attempts:
        out.append("agent_tamper")
    if p.kind != "web" and p.active_s > 60 and p.cpu_mean_cores >= 0.95 and p.io_write_mb <= 1:
        out.append("cpu_pinned")
    return out
