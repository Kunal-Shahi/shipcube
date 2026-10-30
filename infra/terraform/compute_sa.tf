# The default compute service account gets Editor when the Compute API is enabled. Nothing of
# ShipCube's runs as it: every Cloud Run service and job has its own SA. In the control project it
# is still Cloud Functions' build identity (budget-killswitch/deploy.sh), so it keeps only the
# Cloud Build builder role there (#15).
resource "google_project_iam_member_remove" "control_compute_editor" {
  project = var.control_project_id
  role    = "roles/editor"
  member  = "serviceAccount:${data.google_project.control.number}-compute@developer.gserviceaccount.com"
}

resource "google_project_iam_member" "control_compute_builds" {
  project = var.control_project_id
  role    = "roles/cloudbuild.builds.builder"
  member  = "serviceAccount:${data.google_project.control.number}-compute@developer.gserviceaccount.com"
}
