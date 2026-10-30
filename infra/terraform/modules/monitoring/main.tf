# Uptime checks and alert policies for ShipCube's own services. Alerts go to one email channel and
# point at the runbook in deploy/README.md (Operate). Spend is covered separately by the budget kill
# switch (budget.tf), not here.

locals {
  runbook = "Runbook: `deploy/README.md` → Operate."
  # Every Cloud Run service ShipCube runs, for the 5xx alert.
  services_filter = join(" OR ", [for s in keys(var.uptime_paths) : "resource.label.service_name=\"${s}\""])
  sql_filter      = "resource.type=\"cloudsql_database\" AND resource.label.database_id=\"${var.project_id}:${var.sql_instance}\""
}

resource "google_monitoring_notification_channel" "email" {
  project      = var.project_id
  display_name = "ShipCube on-call email"
  type         = "email"
  labels       = { email_address = var.alert_email }
}

resource "google_monitoring_uptime_check_config" "service" {
  for_each = var.uptime_paths

  project          = var.project_id
  display_name     = "ShipCube ${each.key}"
  period           = "300s"
  timeout          = "10s"
  selected_regions = ["USA", "EUROPE", "ASIA_PACIFIC"] # the minimum is 3

  # Private services are checked with an OIDC token, which Monitoring only sends to a Cloud Run
  # resource, not to an arbitrary URL.
  monitored_resource {
    type = contains(var.private_services, each.key) ? "cloud_run_revision" : "uptime_url"
    labels = contains(var.private_services, each.key) ? {
      project_id   = var.project_id
      service_name = each.key
      location     = var.region
      # Monitoring fills these in as empty; declaring them keeps the plan from replacing the check.
      configuration_name = ""
      revision_name      = ""
      } : {
      project_id = var.project_id
      host       = "${each.key}-${var.project_number}.${var.region}.run.app"
    }
  }

  http_check {
    path         = each.value
    port         = 443
    use_ssl      = true
    validate_ssl = !contains(var.private_services, each.key) # only allowed on uptime_url checks
    accepted_response_status_codes {
      status_class = "STATUS_CLASS_2XX"
    }
    dynamic "service_agent_authentication" {
      for_each = contains(var.private_services, each.key) ? [1] : []
      content {
        type = "OIDC_TOKEN"
      }
    }
  }
}

resource "google_monitoring_alert_policy" "down" {
  for_each = var.uptime_paths

  project               = var.project_id
  display_name          = "ShipCube ${each.key} is down"
  combiner              = "OR"
  notification_channels = [google_monitoring_notification_channel.email.id]

  conditions {
    display_name = "Uptime check failing for ${each.key} from 2+ regions"
    condition_threshold {
      filter          = "metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" AND resource.type=\"${contains(var.private_services, each.key) ? "cloud_run_revision" : "uptime_url"}\" AND metric.label.check_id=\"${google_monitoring_uptime_check_config.service[each.key].uptime_check_id}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      duration        = "300s"
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["metric.label.check_id"]
      }
      trigger {
        count = 1
      }
    }
  }

  documentation {
    mime_type = "text/markdown"
    content   = "`${each.key}` failed its uptime check (`${each.value}`) from at least two regions. First steps: `gcloud run services describe ${each.key} --region ${var.region}` and the service's error logs. ${local.runbook}"
  }
}

resource "google_monitoring_alert_policy" "errors" {
  project               = var.project_id
  display_name          = "ShipCube 5xx ratio above 5%"
  combiner              = "OR"
  notification_channels = [google_monitoring_notification_channel.email.id]

  conditions {
    display_name = "5xx / all requests > 5% for 5 min"
    condition_threshold {
      filter                  = "metric.type=\"run.googleapis.com/request_count\" AND resource.type=\"cloud_run_revision\" AND metric.label.response_code_class=\"5xx\" AND (${local.services_filter})"
      denominator_filter      = "metric.type=\"run.googleapis.com/request_count\" AND resource.type=\"cloud_run_revision\" AND (${local.services_filter})"
      comparison              = "COMPARISON_GT"
      threshold_value         = 0.05
      duration                = "300s"
      evaluation_missing_data = "EVALUATION_MISSING_DATA_INACTIVE"
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_RATE"
        cross_series_reducer = "REDUCE_SUM"
        group_by_fields      = ["resource.label.service_name"]
      }
      denominator_aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_RATE"
        cross_series_reducer = "REDUCE_SUM"
        group_by_fields      = ["resource.label.service_name"]
      }
      trigger {
        count = 1
      }
    }
  }

  documentation {
    mime_type = "text/markdown"
    content   = "More than 5% of requests to a ShipCube service returned 5xx for 5 minutes. Check the service's logs at severity>=ERROR; for the gateway, `jsonPayload.message=\"chat\"` lines show the provider, status and attempts. ${local.runbook}"
  }
}

# The worker's own stall signal is its /health (503 after SHIPCUBE_WORKER_STALL_S without a loop
# iteration), covered by its uptime check above. This catches the other way the queue stops draining:
# no worker instance at all (min-instances set to 0, or crash-looping).
resource "google_monitoring_alert_policy" "worker_not_running" {
  project               = var.project_id
  display_name          = "ShipCube worker not running"
  combiner              = "OR"
  notification_channels = [google_monitoring_notification_channel.email.id]

  conditions {
    display_name = "No ${var.worker_service} instance for 15 min"
    condition_absent {
      filter   = "metric.type=\"run.googleapis.com/container/instance_count\" AND resource.type=\"cloud_run_revision\" AND resource.label.service_name=\"${var.worker_service}\""
      duration = "900s"
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_MAX"
        cross_series_reducer = "REDUCE_SUM"
        group_by_fields      = ["resource.label.service_name"]
      }
      trigger {
        count = 1
      }
    }
  }

  documentation {
    mime_type = "text/markdown"
    content   = "`${var.worker_service}` has had no instance for 15 minutes, so queued operations aren't being claimed. If it was scaled to zero on purpose to save cost, scale it back with `--min-instances 1`; otherwise check its revision's Ready condition and logs. ${local.runbook}"
  }
}

resource "google_monitoring_alert_policy" "sql_down" {
  project               = var.project_id
  display_name          = "ShipCube Cloud SQL down"
  combiner              = "OR"
  notification_channels = [google_monitoring_notification_channel.email.id]

  conditions {
    display_name = "${var.sql_instance} not up for 5 min"
    condition_threshold {
      filter          = "metric.type=\"cloudsql.googleapis.com/database/up\" AND ${local.sql_filter}"
      comparison      = "COMPARISON_LT"
      threshold_value = 1
      duration        = "300s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_MIN"
      }
      trigger {
        count = 1
      }
    }
  }

  documentation {
    mime_type = "text/markdown"
    content   = "Cloud SQL `${var.sql_instance}` reports down. `gcloud sql instances describe ${var.sql_instance}` and `gcloud sql operations list -i ${var.sql_instance}`. ${local.runbook}"
  }
}

resource "google_monitoring_alert_policy" "sql_cpu" {
  project               = var.project_id
  display_name          = "ShipCube Cloud SQL CPU above 80%"
  combiner              = "OR"
  notification_channels = [google_monitoring_notification_channel.email.id]

  conditions {
    display_name = "${var.sql_instance} CPU > 80% for 15 min"
    condition_threshold {
      filter          = "metric.type=\"cloudsql.googleapis.com/database/cpu/utilization\" AND ${local.sql_filter}"
      comparison      = "COMPARISON_GT"
      threshold_value = 0.8
      duration        = "900s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_MEAN"
      }
      trigger {
        count = 1
      }
    }
  }

  documentation {
    mime_type = "text/markdown"
    content   = "Cloud SQL `${var.sql_instance}` CPU has been above 80% for 15 minutes. It is shared-core `db-f1-micro`; check Query Insights for the expensive queries before raising `sql_tier`. ${local.runbook}"
  }
}

# A hosted app callable by anyone (allUsers / allAuthenticatedUsers) bypasses IAP. The worker checks
# hourly and logs this exact message at CRITICAL (services/api/shipcube_api/worker.py
# check_exposure); ShipCube never makes such a grant itself (#16).
locals {
  public_service_message = "hosted service is public: anyone can call it past IAP"
}

resource "google_logging_metric" "public_hosted_service" {
  project     = var.project_id
  name        = "shipcube/public_hosted_service"
  description = "Hosted Cloud Run services found callable by allUsers or allAuthenticatedUsers."
  filter      = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${var.worker_service}\" AND severity=CRITICAL AND jsonPayload.message=\"${local.public_service_message}\""
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
    labels {
      key         = "service"
      value_type  = "STRING"
      description = "The exposed service's resource name"
    }
  }
  label_extractors = {
    service = "EXTRACT(jsonPayload.service)"
  }
}

resource "google_monitoring_alert_policy" "public_hosted_service" {
  project               = var.project_id
  display_name          = "ShipCube hosted app is public"
  combiner              = "OR"
  notification_channels = [google_monitoring_notification_channel.email.id]

  conditions {
    display_name = "Worker found a hosted service callable by anyone"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.public_hosted_service.name}\" AND resource.type=\"cloud_run_revision\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_DELTA"
        cross_series_reducer = "REDUCE_SUM"
        group_by_fields      = ["metric.label.service"]
      }
      trigger {
        count = 1
      }
    }
  }

  alert_strategy {
    auto_close = "7200s" # the check repeats hourly; an incident left open means it is still public
  }

  documentation {
    mime_type = "text/markdown"
    content   = "A hosted app's Cloud Run service can be called by `allUsers` or `allAuthenticatedUsers`, which skips IAP. The incident's `service` label names it. Remove the binding at once: `gcloud run services remove-iam-policy-binding SERVICE --project shipcube-apps-ks --region REGION --member allUsers --role roles/run.invoker` (or `allAuthenticatedUsers`), then find who added it in the apps project's Admin Activity audit log (`protoPayload.methodName:\"SetIamPolicy\"`). ${local.runbook}"
  }
}

# Heartbeat for the check above: the worker logs this INFO line after every successful run
# (hourly), with services_checked. No heartbeat for 3 hours means the check stopped (worker down
# or scaled to zero, or the check failing), so a public app would go unnoticed.
locals {
  exposure_heartbeat_message = "exposure check ran"
}

resource "google_logging_metric" "exposure_check_heartbeat" {
  project     = var.project_id
  name        = "shipcube/exposure_check_heartbeat"
  description = "Successful runs of the worker's hourly public-service check."
  filter      = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${var.worker_service}\" AND severity=INFO AND jsonPayload.message=\"${local.exposure_heartbeat_message}\""
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }
}

resource "google_monitoring_alert_policy" "exposure_check_stopped" {
  project               = var.project_id
  display_name          = "ShipCube public-app check stopped"
  combiner              = "OR"
  notification_channels = [google_monitoring_notification_channel.email.id]

  conditions {
    display_name = "No exposure-check heartbeat for 3 h"
    condition_absent {
      filter   = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.exposure_check_heartbeat.name}\" AND resource.type=\"cloud_run_revision\""
      duration = "10800s"
      aggregations {
        alignment_period     = "3600s"
        per_series_aligner   = "ALIGN_DELTA"
        cross_series_reducer = "REDUCE_SUM"
      }
      trigger {
        count = 1
      }
    }
  }

  documentation {
    mime_type = "text/markdown"
    content   = "The worker's hourly check that no hosted app is public hasn't completed for 3 hours, so a public app would go unnoticed. Check that `${var.worker_service}` is running (it may have been scaled to zero on purpose, in which case this is expected), then its logs for `exposure check failed`. ${local.runbook}"
  }
}
