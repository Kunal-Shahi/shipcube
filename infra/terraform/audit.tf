# Audit logging. Admin Activity logs are always on, free, and kept 400 days in the
# _Required bucket. Data Access logs are off by default; turn them on where they
# answer "who read or used what":
#   - Secret Manager reads (who accessed a BYOK key or the token pepper)
#   - Cloud SQL admin data access
#   - IAM (who minted tokens for which service account)
#   - IAP in the apps project (who opened which app)
# Every audit log from both projects is also copied to a dedicated bucket kept 365 days.

locals {
  control_data_access = ["secretmanager.googleapis.com", "cloudsql.googleapis.com", "iam.googleapis.com", "iamcredentials.googleapis.com"]
}

resource "google_project_iam_audit_config" "control" {
  for_each = toset(local.control_data_access)
  project  = var.control_project_id
  service  = each.value
  audit_log_config { log_type = "DATA_READ" }
  audit_log_config { log_type = "DATA_WRITE" }
}

resource "google_project_iam_audit_config" "apps_iap" {
  project = var.apps_project_id
  service = "iap.googleapis.com"
  audit_log_config { log_type = "DATA_READ" }
}

resource "google_logging_project_bucket_config" "audit" {
  project        = var.control_project_id
  location       = "global"
  bucket_id      = "shipcube-audit"
  retention_days = 365
  description    = "Cloud Audit Logs from shipcube-control and shipcube-apps"
  # Not locked: locking is irreversible. Lock once retention is final.
}

resource "google_logging_project_sink" "audit" {
  for_each               = { control = var.control_project_id, apps = var.apps_project_id }
  project                = each.value
  name                   = "audit-to-shipcube-audit"
  destination            = "logging.googleapis.com/${google_logging_project_bucket_config.audit.id}"
  filter                 = "logName:\"cloudaudit.googleapis.com\""
  unique_writer_identity = true
}

# A sink in the apps project writes into the control project's bucket, so its writer
# identity needs permission there. (The control project's own sink does not.)
resource "google_project_iam_member" "audit_sink_writer_apps" {
  project = var.control_project_id
  role    = "roles/logging.bucketWriter"
  member  = google_logging_project_sink.audit["apps"].writer_identity
}
