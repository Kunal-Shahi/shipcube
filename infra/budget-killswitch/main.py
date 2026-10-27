"""Hard spending cap for ShipCube's billing account.

GCP budgets only alert; they never stop spend. This function receives the
budget's Pub/Sub notifications and, once cost reaches CAP, unlinks the
billing account from every project in PROJECTS, which stops all paid usage.
Projects are processed in order, so list the project hosting this function last.

A message with the attribute dryRun=true only checks that we hold the
permission to disable billing on each project; nothing is changed.
"""

import base64
import json
import os

import functions_framework
from google.cloud import billing_v1, resourcemanager_v3

CAP = float(os.environ["CAP"])  # in the billing account currency (INR here)
PROJECTS = [p.strip() for p in os.environ["PROJECTS"].split(",") if p.strip()]
PERMISSION = "resourcemanager.projects.deleteBillingAssignment"


@functions_framework.cloud_event
def on_budget(event):
    msg = event.data["message"]
    attrs = msg.get("attributes") or {}
    data = json.loads(base64.b64decode(msg["data"]).decode())
    cost = float(data.get("costAmount", 0))
    print(f"budget update: cost={cost} cap={CAP} attrs={attrs}")

    if attrs.get("dryRun") == "true":
        check_permissions()
        return
    if cost < CAP:
        return

    billing = billing_v1.CloudBillingClient()
    for project in PROJECTS:
        name = f"projects/{project}"
        try:
            if not billing.get_project_billing_info(name=name).billing_enabled:
                print(f"{project}: billing already disabled")
                continue
        except Exception as e:  # a failed read must never block the shutdown
            print(f"{project}: could not read billing info ({e}); disabling anyway")
        billing.update_project_billing_info(
            name=name,
            project_billing_info=billing_v1.ProjectBillingInfo(billing_account_name=""),
        )
        print(f"{project}: billing DISABLED (cost {cost} >= cap {CAP})")


def check_permissions():
    projects = resourcemanager_v3.ProjectsClient()
    billing = billing_v1.CloudBillingClient()
    for project in PROJECTS:
        resp = projects.test_iam_permissions(resource=f"projects/{project}", permissions=[PERMISSION])
        ok = PERMISSION in resp.permissions
        enabled = billing.get_project_billing_info(name=f"projects/{project}").billing_enabled
        print(f"dry run: {project}: {'OK' if ok else 'MISSING ' + PERMISSION}, billing_enabled={enabled}")
