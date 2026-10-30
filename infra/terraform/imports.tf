# Resources created by hand before Terraform owned them (Oct 27-28, 2026).
# Import blocks are idempotent: once imported they are no-ops and can be removed.

import {
  to = google_pubsub_topic.budget
  id = "projects/${var.control_project_id}/topics/budget-alerts"
}

import {
  to = google_billing_budget.hard_cap
  id = "billingAccounts/${var.billing_account_id}/budgets/${var.existing_budget_id}"
}

import {
  to = google_service_account.killswitch
  id = "projects/${var.control_project_id}/serviceAccounts/budget-killswitch@${var.control_project_id}.iam.gserviceaccount.com"
}

import {
  for_each = toset(["meta-api-key", "token-hmac-pepper"])
  to       = google_secret_manager_secret.platform[each.value]
  id       = "projects/${var.control_project_id}/secrets/${each.value}"
}

# Created by the networking/platform sessions while building the deploy path (Oct 28).
import {
  to = google_service_account.api
  id = "projects/${var.control_project_id}/serviceAccounts/sc-api@${var.control_project_id}.iam.gserviceaccount.com"
}

import {
  to = google_artifact_registry_repository.apps
  id = "projects/${var.apps_project_id}/locations/${var.region}/repositories/shipcube"
}

# Adopted Oct 28 (Kunal: "create the shipcube-apps project first" — it already existed).
import {
  to = google_project.apps
  id = var.apps_project_id
}

import {
  to = google_storage_bucket.uploads
  id = "${var.apps_project_id}/${var.uploads_bucket_name}"
}

import {
  to = google_sql_user.postgres
  id = "${var.control_project_id}/shipcube-pg/postgres"
}
