# Runtime abuse alerts on hosted apps (users' Cloud Run services in the apps project): crypto-mining
# (CPU pegged for half an hour), bulk egress, and request floods. Each incident names the service; the
# documentation says how to find its workspace and how to cut it off the way ShipCube's own delete does
# (internal-only ingress: nothing external reaches it, idle instances scale to zero, restorable).

# The policies live in this (control) project with the existing email channel, so the apps project
# joins its metrics scope. sc-api already reads apps-project metrics (monitoring.viewer there, iam.tf).
resource "google_monitoring_monitored_project" "apps" {
  metrics_scope = "locations/global/metricsScopes/${var.project_id}"
  name          = var.apps_project_id
}

locals {
  hosted_filter = "resource.type=\"cloud_run_revision\" AND resource.label.project_id=\"${var.apps_project_id}\" AND metadata.user_labels.\"managed-by\"=\"shipcube\""
  # Incident documentation: $${resource.label.*} are filled in per incident (the policies group by them).
  abuse_triage = <<-EOT
    **Service:** `$${resource.label.service_name}` in `$${resource.label.location}` (project `${var.apps_project_id}`).

    **Workspace:** the service's `shipcube-workspace` label (first 8 characters of the workspace id); `shipcube-app` is the app name:
    `gcloud run services describe $${resource.label.service_name} --project ${var.apps_project_id} --region $${resource.label.location} --format='value(metadata.labels)'`

    **Cut it off** (what ShipCube's delete does; restorable, and idle instances then scale to zero):
    `gcloud run services update $${resource.label.service_name} --project ${var.apps_project_id} --region $${resource.label.location} --ingress internal`

    Then look at its logs and recent revisions, and suspend the workspace if it's abuse. ${local.runbook}
  EOT
}

resource "google_monitoring_alert_policy" "hosted_cpu_pegged" {
  project               = var.project_id
  display_name          = "ShipCube hosted app CPU pegged (possible mining)"
  combiner              = "OR"
  notification_channels = [google_monitoring_notification_channel.email.id]
  depends_on            = [google_monitoring_monitored_project.apps]

  conditions {
    display_name = "Median instance CPU > ${var.abuse_cpu_utilization * 100}% for ${var.abuse_cpu_minutes} min"
    condition_threshold {
      filter                  = "metric.type=\"run.googleapis.com/container/cpu/utilizations\" AND ${local.hosted_filter}"
      comparison              = "COMPARISON_GT"
      threshold_value         = var.abuse_cpu_utilization
      duration                = "${var.abuse_cpu_minutes * 60}s"
      evaluation_missing_data = "EVALUATION_MISSING_DATA_INACTIVE"
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_PERCENTILE_50"
        cross_series_reducer = "REDUCE_MAX"
        group_by_fields      = ["resource.label.service_name", "resource.label.location"]
      }
      trigger {
        count = 1
      }
    }
  }

  documentation {
    mime_type = "text/markdown"
    subject   = "ShipCube: $${resource.label.service_name} CPU pegged for ${var.abuse_cpu_minutes} min"
    content   = "A hosted app's instances have run near 100% CPU for ${var.abuse_cpu_minutes} minutes. Hosted apps only get CPU while serving requests, so this means CPU-bound requests kept up for half an hour: the signature of crypto-mining (or a runaway loop).\n\n${local.abuse_triage}"
  }
}

resource "google_monitoring_alert_policy" "hosted_egress_spike" {
  project               = var.project_id
  display_name          = "ShipCube hosted app egress spike"
  combiner              = "OR"
  notification_channels = [google_monitoring_notification_channel.email.id]
  depends_on            = [google_monitoring_monitored_project.apps]

  conditions {
    display_name = "Outbound > ${var.abuse_egress_bytes_per_s} B/s for 10 min"
    condition_threshold {
      filter                  = "metric.type=\"run.googleapis.com/container/network/sent_bytes_count\" AND ${local.hosted_filter}"
      comparison              = "COMPARISON_GT"
      threshold_value         = var.abuse_egress_bytes_per_s
      duration                = "600s"
      evaluation_missing_data = "EVALUATION_MISSING_DATA_INACTIVE"
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_RATE"
        cross_series_reducer = "REDUCE_SUM"
        group_by_fields      = ["resource.label.service_name", "resource.label.location"]
      }
      trigger {
        count = 1
      }
    }
  }

  documentation {
    mime_type = "text/markdown"
    subject   = "ShipCube: $${resource.label.service_name} sending unusual outbound traffic"
    content   = "A hosted app has sent more than ${var.abuse_egress_bytes_per_s} bytes/s for 10 minutes, far above an internal tool's normal responses. Possible spam, a DDoS source, or data exfiltration; it also costs egress.\n\n${local.abuse_triage}"
  }
}

resource "google_monitoring_alert_policy" "hosted_request_spike" {
  project               = var.project_id
  display_name          = "ShipCube hosted app request spike"
  combiner              = "OR"
  notification_channels = [google_monitoring_notification_channel.email.id]
  depends_on            = [google_monitoring_monitored_project.apps]

  conditions {
    display_name = "Requests > ${var.abuse_requests_per_s}/s for 10 min"
    condition_threshold {
      filter                  = "metric.type=\"run.googleapis.com/request_count\" AND ${local.hosted_filter}"
      comparison              = "COMPARISON_GT"
      threshold_value         = var.abuse_requests_per_s
      duration                = "600s"
      evaluation_missing_data = "EVALUATION_MISSING_DATA_INACTIVE"
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_RATE"
        cross_series_reducer = "REDUCE_SUM"
        group_by_fields      = ["resource.label.service_name", "resource.label.location"]
      }
      trigger {
        count = 1
      }
    }
  }

  documentation {
    mime_type = "text/markdown"
    subject   = "ShipCube: $${resource.label.service_name} request spike"
    content   = "A hosted app has served more than ${var.abuse_requests_per_s} requests/s for 10 minutes (requests that passed IAP). Internal tools rarely see this; it may be a scraper or load generator run by someone with access, or the app being used as a proxy.\n\n${local.abuse_triage}"
  }
}
