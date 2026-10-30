#!/usr/bin/env python3
"""Static guard on infra/terraform: ShipCube's own service identities never get broad IAM roles.

Fails if a google_project_iam_member / _binding grants a forbidden role at project level, or grants
`roles/iam.serviceAccountUser` / `roles/iam.serviceAccountTokenCreator` project-wide outside the
projects where that is the point (links.tf: every SA there is a link deployer). Per-resource grants
(on one SA, one secret, one repo) aren't checked: they are the narrow form this guard pushes toward.
Standard library only; runs in CI's infra job.

    python3 infra/tests/iam_guard.py [infra/terraform]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Roles no ShipCube identity may hold on a whole project (#15, #16).
FORBIDDEN = {
    "roles/owner", "roles/editor",
    "roles/iam.serviceAccountAdmin", "roles/iam.securityAdmin", "roles/resourcemanager.projectIamAdmin",
    "roles/secretmanager.admin",
}
# Project-wide "act as / impersonate any SA" is allowed only where every SA is meant for it.
ACT_AS = {"roles/iam.serviceAccountUser", "roles/iam.serviceAccountTokenCreator"}
ACT_AS_ALLOWED_FILES = {"links.tf"}

# Project-level roles that read secret values: always under a resource-name condition, and for the
# gateway only on AI keys (ws-ai-*), never warehouse credentials (#15).
SECRET_READ_ROLES = {"roles/secretmanager.secretAccessor", "roles/secretmanager.secretVersionManager",
                     "roles/secretmanager.admin"}
SECRET_READ_CUSTOM = ("workspace_secrets",)  # custom roles (by resource name) that include versions.access
GATEWAY_MEMBER = "google_service_account.gateway.member"
AI_KEY_PREFIX = ("ai_key_secrets_prefix", "/secrets/ws-ai-")

# Known exceptions, each tied to the issue that removes it. Delete the entry with the grant.
KNOWN: dict[tuple[str, str], str] = {}

BLOCK = re.compile(r'resource\s+"(google_project_iam_(?:member|binding))"\s+"([^"]+)"\s*\{', re.M)


def blocks(text: str):
    for m in BLOCK.finditer(text):
        depth, i = 1, m.end()
        while depth and i < len(text):
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        yield m[1], m[2], text[m.end():i - 1]


def roles_in(body: str) -> set[str]:
    # role = "..." and the literal roles of a for_each = toset([...]) list
    found = set(re.findall(r'role\s*=\s*"([^"$]+)"', body))
    for lst in re.findall(r"toset\(\[(.*?)\]\)", body, re.S):
        found |= set(re.findall(r'"(roles/[^"]+)"', lst))
    return found


def secret_problems(where: str, body: str) -> list[str]:
    role = re.search(r"role\s*=\s*([^\n]+)", body)
    role = role[1].strip() if role else ""
    reads = role.strip('"') in SECRET_READ_ROLES or any(c in role for c in SECRET_READ_CUSTOM)
    if not reads:
        return []
    cond = re.search(r"condition\s*\{(.*?)\n\s*\}", body, re.S)
    if not cond:
        return [f"{where} reads secrets project-wide without a resource-name condition"]
    if GATEWAY_MEMBER in body and not any(p in cond[1] for p in AI_KEY_PREFIX):
        return [f"{where} lets the gateway read secrets other than AI keys (condition on ws-ai-* only)"]
    return []


def check(root: Path) -> list[str]:
    problems = []
    for f in sorted(root.rglob("*.tf")):
        if ".terraform" in f.parts:
            continue
        for kind, name, body in blocks(f.read_text()):
            if kind == "google_project_iam_member":
                problems += secret_problems(f"{f.relative_to(root)}: {kind}.{name}", body)
            for role in roles_in(body):
                if (f"{kind}.{name}", role) in KNOWN:
                    continue
                where = f"{f.relative_to(root)}: {kind}.{name}"
                if role in FORBIDDEN:
                    problems.append(f"{where} grants {role} on a whole project")
                elif role in ACT_AS and f.name not in ACT_AS_ALLOWED_FILES:
                    problems.append(f"{where} grants {role} on a whole project: grant it on each service account instead")
    return problems


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "terraform")
    problems = check(root)
    for p in problems:
        print(f"FAIL {p}")
    for (res, role), issue in KNOWN.items():
        print(f"KNOWN {res} {role} (to be removed by {issue})")
    if problems:
        sys.exit(1)
    print(f"iam guard: no broad project-level grants in {root}")


if __name__ == "__main__":
    main()
