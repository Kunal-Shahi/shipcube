# Source uploads for deploys (signed PUT URLs from sc-api, read by Cloud Build as sc-builder).
# Objects are deleted after a day. Created by networking's gcp_setup.sh, adopted here.
resource "google_storage_bucket" "uploads" {
  project                     = var.apps_project_id
  name                        = var.uploads_bucket_name
  location                    = upper(var.region)
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  lifecycle_rule {
    action { type = "Delete" }
    condition { age = 1 }
  }
}

resource "google_storage_bucket_iam_member" "uploads_signer" {
  bucket = google_storage_bucket.uploads.name
  role   = "roles/storage.objectCreator"
  member = google_service_account.api.member
}

# Deploys run in the worker: it checks uploaded sources exist and writes inline sources as tarballs.
resource "google_storage_bucket_iam_member" "uploads_worker" {
  for_each = toset(["roles/storage.objectViewer", "roles/storage.objectCreator"])
  bucket   = google_storage_bucket.uploads.name
  role     = each.value
  member   = google_service_account.worker.member
}

# The worker's profiler janitor deletes leftover profiler sources, which live under
# ws/<workspace uuid>/profiler/ (profiler builds run as the workspace's builder, #69/#72).
# Delete only, and only there: never bucket-wide. Objects also expire after a day anyway.
resource "google_project_iam_custom_role" "profiler_source_janitor" {
  project     = var.apps_project_id
  role_id     = "shipcubeProfilerSourceJanitor"
  title       = "ShipCube profiler source janitor"
  description = "Delete profiler source tarballs (granted on the uploads bucket under a ws/*/profiler/ condition)."
  permissions = ["storage.objects.delete"]
}

resource "google_storage_bucket_iam_member" "uploads_worker_profiler_janitor" {
  bucket = google_storage_bucket.uploads.name
  role   = google_project_iam_custom_role.profiler_source_janitor.id
  member = google_service_account.worker.member
  condition {
    title       = "profiler sources only"
    description = "ws/<workspace uuid>/profiler/ objects"
    expression  = "resource.name.matches(\"^projects/_/buckets/${replace(google_storage_bucket.uploads.name, ".", "\\\\.")}/objects/ws/[0-9a-f-]+/profiler/[^/]+$\")"
  }
}
