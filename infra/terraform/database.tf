resource "google_sql_database_instance" "main" {
  project             = var.control_project_id
  name                = "shipcube-pg"
  region              = var.region
  database_version    = "POSTGRES_16"
  deletion_protection = true

  settings {
    edition           = "ENTERPRISE"
    tier              = "db-f1-micro"
    disk_type         = "PD_SSD"
    disk_size         = 10
    availability_type = "ZONAL"

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = false
    }

    ip_configuration {
      ipv4_enabled = true # reached only through the Cloud SQL connector; no authorized networks
      ssl_mode     = "ENCRYPTED_ONLY"
    }

    database_flags {
      name  = "cloudsql.iam_authentication"
      value = "on"
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
