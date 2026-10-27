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

# Legacy shared builder: reads every workspace's uploads (C-1). Removed by legacy_shared_builder=false.
resource "google_storage_bucket_iam_member" "uploads_builder" {
  count  = var.legacy_shared_builder ? 1 : 0
  bucket = google_storage_bucket.uploads.name
  role   = "roles/storage.objectViewer"
  member = google_service_account.builder.member
}

moved {
  from = google_storage_bucket_iam_member.uploads_builder
  to   = google_storage_bucket_iam_member.uploads_builder[0]
}
