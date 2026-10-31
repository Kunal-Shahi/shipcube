# Uptime checks and alert policies for the control plane's Cloud Run services and Cloud SQL. The
# Monitoring API is enabled in apis.tf.

data "google_project" "control" {
  project_id = var.control_project_id
}

module "monitoring" {
  source = "./modules/monitoring"

  project_id      = var.control_project_id
  project_number  = data.google_project.control.number
  apps_project_id = var.apps_project_id
  region          = var.region
  alert_email     = var.alert_email
  sql_instance    = google_sql_database_instance.main.name
  depends_on      = [google_project_service.control]
}
