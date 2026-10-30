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

# Apps project: nothing runs as the default compute SA (apps, builds and profiler jobs all have
# their own SAs), so it keeps no role at all (#16).
resource "google_project_iam_member_remove" "apps_compute_editor" {
  project = var.apps_project_id
  role    = "roles/editor"
  member  = "serviceAccount:${google_project.apps.number}-compute@developer.gserviceaccount.com"
}

# The App Engine default SA also gets Editor when that API is enabled. There is no App Engine app in
# the control project and nothing runs as it, so it keeps no role (H-2 residual, red-team pentest).
resource "google_project_iam_member_remove" "control_appspot_editor" {
  project = var.control_project_id
  role    = "roles/editor"
  member  = "serviceAccount:${var.control_project_id}@appspot.gserviceaccount.com"
}
