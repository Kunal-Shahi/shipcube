# Budgets alert; they do not cap spend. The worker subscribes to this topic and,
# past the last threshold, pauses hosted apps (max-instances=0) and blocks deploys.

resource "google_pubsub_topic" "budget" {
  project    = var.control_project_id
  name       = "budget-alerts"
  depends_on = [google_project_service.control]
}

data "google_project" "apps" {
  project_id = var.apps_project_id
}

data "google_project" "control" {
  project_id = var.control_project_id
}

resource "google_billing_budget" "credits" {
  billing_account = var.billing_account_id
  display_name    = "ShipCube credit pool"

  budget_filter {
    projects               = ["projects/${data.google_project.apps.number}", "projects/${data.google_project.control.number}"]
    credit_types_treatment = "EXCLUDE_ALL_CREDITS" # count gross spend, so the credits are what we're measuring
  }

  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(var.budget_amount_usd)
    }
  }

  dynamic "threshold_rules" {
    for_each = [0.17, 0.5, 0.83, 1.0]
    content {
      threshold_percent = threshold_rules.value
    }
  }

  all_updates_rule {
    pubsub_topic = google_pubsub_topic.budget.id
  }
}
