resource "google_sql_database_instance" "main" {
  project             = var.control_project_id
  name                = "shipcube-pg"
  region              = var.region
  database_version    = "POSTGRES_16"
  deletion_protection = true # Terraform refuses to destroy it

  settings {
    edition                     = "ENTERPRISE"
    tier                        = var.sql_tier
    disk_type                   = "PD_SSD"
    disk_size                   = 10
    disk_autoresize             = true
    disk_autoresize_limit       = 50
    availability_type           = var.sql_availability
    deletion_protection_enabled = true # the API refuses to delete it too

    backup_configuration {
      enabled                        = true
      start_time                     = "20:30" # UTC = 02:00 IST
      location                       = var.region
      point_in_time_recovery_enabled = true
      transaction_log_retention_days = 7
      backup_retention_settings {
        retained_backups = 14
        retention_unit   = "COUNT"
      }
    }

    maintenance_window {
      day          = 7  # Sunday
      hour         = 21 # UTC = 02:30 IST
      update_track = "stable"
    }

    insights_config {
      query_insights_enabled  = true
      record_application_tags = true
    }

    ip_configuration {
      ipv4_enabled = true # reached only through the Cloud SQL connector; no authorized networks
      ssl_mode     = "ENCRYPTED_ONLY"
    }

    database_flags {
      name  = "cloudsql.iam_authentication"
      value = "on"
    }

    # pgaudit: DDL and role changes from any session go to Cloud Logging. Row-level
    # actions are recorded by the application's own audit_log table instead.
    database_flags {
      name  = "cloudsql.enable_pgaudit"
      value = "on"
    }
    database_flags {
      name  = "pgaudit.log"
      value = "ddl,role"
    }
    database_flags {
      name  = "log_min_duration_statement"
      value = "1000" # log statements slower than 1 s
    }
  }

  depends_on = [google_project_service.control]
}

resource "google_sql_database" "shipcube" {
  project  = var.control_project_id
  instance = google_sql_database_instance.main.name
  name     = "shipcube"
}

# IAM database users: no passwords. Postgres roles and grants are set by migrations.
resource "google_sql_user" "iam" {
  for_each = {
    api    = google_service_account.api.email
    worker = google_service_account.worker.email
  }
  project  = var.control_project_id
  instance = google_sql_database_instance.main.name
  name     = trimsuffix(each.value, ".gserviceaccount.com")
  type     = "CLOUD_IAM_SERVICE_ACCOUNT"
}
