#!/usr/bin/env python3
"""Static check of the profiler sandbox's Terraform (docs/research/profiler-sandbox.md §3).

Asserts, over every .tf file under infra/terraform:
  1. No egress path from the sandbox network: no allow firewall rule, Cloud Router/NAT,
     VPC peering, Serverless VPC connector, PSC/forwarding rule, route, VPN or
     interconnect attachment references the sandbox network; its only firewall rule is
     the deny-all egress rule; the subnet keeps Private Google Access off.
  2. The sandbox runtime SA (profiler-sandbox) has zero bindings: it never appears as
     a member of any IAM resource, and no authoritative IAM resource names it.

Run: python3 infra/tests/profiler_sandbox_check.py [infra/terraform]
packages/profiler/tests/test_infra_sandbox.py runs it in CI.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

NETWORK_REFS = ("google_compute_network.sandbox", "google_compute_subnetwork.sandbox", "profiler-no-egress")
SA_REFS = ("google_service_account.sandbox", "profiler-sandbox@", "SHIPCUBE_PROFILER_SERVICE_ACCOUNT")
# What may reference the sandbox network, all in modules/profiler/main.tf.
ALLOWED_NETWORK_USERS = {("google_compute_subnetwork", "sandbox"), ("google_compute_firewall", "deny_egress")}
EGRESS_TYPES = ("google_compute_router", "google_compute_router_nat", "google_compute_network_peering",
                "google_vpc_access_connector", "google_compute_forwarding_rule",
                "google_compute_global_forwarding_rule", "google_compute_route", "google_compute_vpn_gateway",
                "google_compute_ha_vpn_gateway", "google_compute_interconnect_attachment",
                "google_service_networking_connection", "google_compute_service_attachment",
                "google_network_connectivity_spoke", "google_compute_network_attachment")

# The private build pool's VPC (modules/profiler/build.tf): peered with Google's service
# network for the pool, and nothing else; no router, NAT, routes or firewall allow rules.
BUILD_NETWORK_REFS = ("google_compute_network.build",)
ALLOWED_BUILD_NETWORK_USERS = {("google_compute_global_address", "build_peering"),
                               ("google_service_networking_connection", "build"),
                               ("google_cloudbuild_worker_pool", "profiler")}

_BLOCK = re.compile(r'^\s*resource\s+"([^"]+)"\s+"([^"]+)"\s*\{', re.M)


def _blocks(text: str):
    """(type, name, body) for every resource block, by brace matching."""
    for m in _BLOCK.finditer(text):
        depth, i = 1, m.end()
        while depth and i < len(text):
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        yield m.group(1), m.group(2), text[m.end():i - 1]


def _strip_comments(text: str) -> str:
    return re.sub(r"(?m)(^|\s)(#|//).*$", r"\1", text)


def _member_lines(body: str) -> str:
    """The member side of an IAM resource (member, members, or a policy's bindings)."""
    return "\n".join(ln for ln in body.splitlines() if re.match(r"\s*(member|members)\s*=", ln))


def check(root: Path) -> list[str]:
    problems: list[str] = []
    resources = []
    for f in sorted(root.rglob("*.tf")):
        if ".terraform" in f.parts:
            continue
        for t, n, body in _blocks(_strip_comments(f.read_text())):
            resources.append((f.relative_to(root), t, n, body))

    found = {(t, n) for _, t, n, _ in resources}
    for need in [("google_compute_network", "sandbox"), *ALLOWED_NETWORK_USERS, ("google_service_account", "sandbox")]:
        if need not in found:
            problems.append(f"missing {need[0]}.{need[1]} (the sandbox moved or was renamed; update this check)")

    for f, t, n, body in resources:
        where = f"{f}: {t}.{n}"
        refs_network = any(r in body for r in NETWORK_REFS)
        # 1. Egress paths.
        if refs_network and (t, n) not in ALLOWED_NETWORK_USERS and t != "google_compute_network":
            problems.append(f"{where} references the sandbox network; only its subnet and deny-all firewall may")
        if t in EGRESS_TYPES and refs_network:
            problems.append(f"{where} would give the sandbox network a way out")
        if any(r in body for r in BUILD_NETWORK_REFS) and (t, n) not in ALLOWED_BUILD_NETWORK_USERS:
            problems.append(f"{where} references the profiler build network; only its pool and peering may")
        if (t, n) == ("google_cloudbuild_worker_pool", "profiler") and not re.search(
                r"no_external_ip\s*=\s*true", body):
            problems.append(f"{where} must set no_external_ip = true")
        if (t, n) == ("google_compute_firewall", "deny_egress"):
            if re.search(r"(?m)^\s*allow\s*\{", body):
                problems.append(f"{where} has an allow block")
            for want in (r'direction\s*=\s*"EGRESS"', r'protocol\s*=\s*"all"', r'"0\.0\.0\.0/0"',
                         r"(?m)^\s*deny\s*\{"):
                if not re.search(want, body):
                    problems.append(f"{where} lost {want!r}")
            if re.search(r"(?m)^\s*disabled\s*=\s*true", body):
                problems.append(f"{where} is disabled")
        if (t, n) == ("google_compute_subnetwork", "sandbox") and not re.search(
                r"private_ip_google_access\s*=\s*false", body):
            problems.append(f"{where} must set private_ip_google_access = false")
        if (t, n) == ("google_compute_network", "sandbox") and not re.search(
                r"auto_create_subnetworks\s*=\s*false", body):
            problems.append(f"{where} must set auto_create_subnetworks = false")
        # 2. The sandbox SA has zero bindings.
        if "_iam_" in t:
            if any(r in _member_lines(body) for r in SA_REFS):
                problems.append(f"{where} grants a role to the sandbox SA; it must have none")
            if t.endswith(("_iam_policy", "_iam_binding")) and any(r in body for r in SA_REFS) \
                    and "service_account_id" not in body:
                problems.append(f"{where} is authoritative IAM that names the sandbox SA")
        if t == "google_service_account_iam_member" and "google_service_account.sandbox.name" in body:
            role = re.search(r'role\s*=\s*"([^"]+)"', body)
            if role and role.group(1) != "roles/iam.serviceAccountUser":
                problems.append(f"{where} grants {role.group(1)} on the sandbox SA; only actAs for the estimator")
    return problems


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "terraform"
    problems = check(root)
    for p in problems:
        print("FAIL", p)
    if not problems:
        print(f"ok: profiler sandbox has no egress path and its SA has zero bindings ({root})")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
