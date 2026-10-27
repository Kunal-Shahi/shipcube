# The apps project was created by hand (no organization to create it under) and is adopted
# here. deletion_policy = PREVENT: Terraform will refuse to delete it.
resource "google_project" "apps" {
  project_id      = var.apps_project_id
  name            = "ShipCube apps"
  billing_account = var.billing_account_id
  deletion_policy = "PREVENT"
  labels          = { owner = "shipcube", role = "apps" }

  lifecycle {
    # Create-time only; the project already exists with its default network.
    ignore_changes = [auto_create_network]
  }
}

module "profiler" {
  source = "./modules/profiler"

  project_id        = google_project.apps.project_id
  region            = var.region
  ml_bucket_project = var.control_project_id
  ml_bucket_name    = var.ml_bucket_name
  estimator_members = [google_service_account.api.member, google_service_account.worker.member]
  depends_on        = [google_project_service.apps]
}
