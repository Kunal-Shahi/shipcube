# Hard spending cap. Budgets only alert, so a Pub/Sub kill switch
# (infra/budget-killswitch, deployed by its deploy.sh) unlinks billing from every
# project in var.killswitch_projects once gross spend reaches var.budget_amount.
# These resources were first created with gcloud on Oct 27-28, 2026 and are adopted
# by the import blocks in imports.tf.

resource "google_pubsub_topic" "budget" {
  project    = var.control_project_id
  name       = "budget-alerts"
  depends_on = [google_project_service.control]
}

resource "google_billing_budget" "hard_cap" {
  billing_account = var.billing_account_id
  display_name    = "Hard cap INR 24000 (kill switch)"

  budget_filter {
    # No project filter: every project on the billing account counts.
    credit_types_treatment = "EXCLUDE_ALL_CREDITS" # gross spend, i.e. how much credit is used
    custom_period {
      start_date {
        year  = tonumber(split("-", var.budget_period.start)[0])
        month = tonumber(split("-", var.budget_period.start)[1])
        day   = tonumber(split("-", var.budget_period.start)[2])
      }
      end_date {
        year  = tonumber(split("-", var.budget_period.end)[0])
        month = tonumber(split("-", var.budget_period.end)[1])
        day   = tonumber(split("-", var.budget_period.end)[2])
      }
    }
  }

  amount {
    specified_amount {
      currency_code = var.budget_currency
      units         = tostring(var.budget_amount)
    }
  }

  dynamic "threshold_rules" {
    for_each = [0.25, 0.5, 0.75, 0.9, 1.0]
    content {
      threshold_percent = threshold_rules.value
      spend_basis       = "CURRENT_SPEND"
    }
  }

  all_updates_rule {
    pubsub_topic   = google_pubsub_topic.budget.id
    schema_version = "1.0"
  }
}

resource "google_service_account" "killswitch" {
  project      = var.control_project_id
  account_id   = "budget-killswitch"
  display_name = "Budget kill switch (disables billing at cap)"
}

# Unlinking billing needs resourcemanager.projects.deleteBillingAssignment
# (billing.projectManager); reading billing status needs resourcemanager.projects.get (browser).
resource "google_project_iam_member" "killswitch" {
  for_each = { for pair in setproduct(var.killswitch_projects, ["roles/billing.projectManager", "roles/browser"]) : "${pair[0]}/${pair[1]}" => pair }
  project  = each.value[0]
  role     = each.value[1]
  member   = google_service_account.killswitch.member
}
