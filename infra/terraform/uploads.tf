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
  # IAM conditions have no regex (matches() and contains() fail to compile), so: the text
  # between ws/ and /profiler/ must equal the first segment after ws/, i.e. exactly one
  # segment, followed directly by profiler/. Checked with IAM's lintPolicy.
  condition {
    title       = "profiler sources only"
    description = "ws/<one segment>/profiler/ objects"
    expression  = <<-EOT
      resource.name.startsWith("${local.uploads_objects}/ws/") &&
      resource.name.extract("${local.uploads_objects}/ws/{ws}/profiler/") != "" &&
      resource.name.extract("${local.uploads_objects}/ws/{ws}/profiler/") == resource.name.extract("${local.uploads_objects}/ws/{ws}/")
    EOT
  }
}

# The worker deletes a deploy's source once its build has used it (or the deploy finally failed), an
# estimate's upload an hour after the estimate, and any leftovers at purge (worker._delete_sources).
# Delete only, and only a workspace's uploads/ and inline/ objects: ws/<one segment>/uploads/... or
# ws/<one segment>/inline/..., the same one-segment extract() check as the profiler janitor above.
# Never bucket-wide; the one-day lifecycle rule stays as the backstop.
resource "google_project_iam_custom_role" "source_cleanup" {
  project     = var.apps_project_id
  role_id     = "shipcubeSourceCleanup"
  title       = "ShipCube source cleanup"
  description = "Delete used source tarballs (granted on the uploads bucket under a ws/*/uploads|inline/ condition)."
  permissions = ["storage.objects.delete"]
}

resource "google_storage_bucket_iam_member" "uploads_worker_source_cleanup" {
  bucket = google_storage_bucket.uploads.name
  role   = google_project_iam_custom_role.source_cleanup.id
  member = google_service_account.worker.member
  condition {
    title       = "workspace sources only"
    description = "ws/<one segment>/uploads/ and ws/<one segment>/inline/ objects"
    expression  = <<-EOT
      resource.name.startsWith("${local.uploads_objects}/ws/") && (
        (resource.name.extract("${local.uploads_objects}/ws/{ws}/uploads/") != "" &&
         resource.name.extract("${local.uploads_objects}/ws/{ws}/uploads/") == resource.name.extract("${local.uploads_objects}/ws/{ws}/")) ||
        (resource.name.extract("${local.uploads_objects}/ws/{ws}/inline/") != "" &&
         resource.name.extract("${local.uploads_objects}/ws/{ws}/inline/") == resource.name.extract("${local.uploads_objects}/ws/{ws}/"))
      )
    EOT
  }
}

locals {
  uploads_objects = "projects/_/buckets/${google_storage_bucket.uploads.name}/objects"
}
