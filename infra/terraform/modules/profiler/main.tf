# Profiler sandbox: everything packages/profiler/deploy/RESOURCES.md (PR #7) lists.
# CloudRunJobRunner creates and deletes a job `prof-<estimate id>` per estimate; those jobs
# are not managed here.

# --- Network with no way out -------------------------------------------------

resource "google_compute_network" "sandbox" {
  project                 = var.project_id
  name                    = "profiler-no-egress"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "sandbox" {
  project                  = var.project_id
  name                     = "profiler"
  region                   = var.region
  network                  = google_compute_network.sandbox.id
  ip_cidr_range            = var.subnet_cidr
  private_ip_google_access = false
}

resource "google_compute_firewall" "deny_egress" {
  project            = var.project_id
  name               = "profiler-no-egress-deny-egress"
  network            = google_compute_network.sandbox.name
  direction          = "EGRESS"
  priority           = 100
  destination_ranges = ["0.0.0.0/0"]
  deny {
    protocol = "all"
  }
}

# --- Identities --------------------------------------------------------------

resource "google_service_account" "sandbox" {
  project      = var.project_id
  account_id   = "profiler-sandbox"
  display_name = "Profiler job runtime (no roles, no secrets)"
}

resource "google_service_account" "build" {
  project      = var.project_id
  account_id   = "profiler-build"
  display_name = "Cloud Build for profiler images"
}

# --- Images ------------------------------------------------------------------

resource "google_artifact_registry_repository" "profiler" {
  project       = var.project_id
  location      = var.region
  repository_id = "profiler"
  format        = "DOCKER"
  description   = "Short-lived images for profiling runs"

  cleanup_policies {
    id     = "delete-after-7-days"
    action = "DELETE"
    condition {
      older_than = "604800s"
    }
  }
}

# --- ML bucket (control project) ----------------------------------------------

resource "google_storage_bucket" "ml" {
  project                     = var.ml_bucket_project
  name                        = var.ml_bucket_name
  location                    = upper(var.region)
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  lifecycle_rule {
    action { type = "Delete" }
    condition {
      age            = 1
      matches_prefix = ["profiler-src/"]
    }
  }
}

locals {
  src_prefix    = "projects/_/buckets/${var.ml_bucket_name}/objects/profiler-src/"
  models_prefix = "projects/_/buckets/${var.ml_bucket_name}/objects/models/workload-classifier/"
}

# --- IAM: build identity -----------------------------------------------------

resource "google_artifact_registry_repository_iam_member" "build_writer" {
  project    = var.project_id
  location   = var.region
  repository = google_artifact_registry_repository.profiler.repository_id
  role       = "roles/artifactregistry.writer"
  member     = google_service_account.build.member
}

resource "google_project_iam_member" "build_logs" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = google_service_account.build.member
}

resource "google_storage_bucket_iam_member" "build_reads_src" {
  bucket = google_storage_bucket.ml.name
  role   = "roles/storage.objectViewer"
  member = google_service_account.build.member
  condition {
    title      = "profiler-src only"
    expression = "resource.name.startsWith(\"${local.src_prefix}\")"
  }
}

# --- IAM: whoever runs estimate() ---------------------------------------------

resource "google_project_iam_member" "estimator" {
  for_each = { for pair in setproduct(var.estimator_members, ["roles/run.developer", "roles/cloudbuild.builds.editor", "roles/logging.viewer"]) : "${pair[0]}|${pair[1]}" => pair }
  project  = var.project_id
  member   = each.value[0]
  role     = each.value[1]
}

resource "google_service_account_iam_member" "estimator_acts_as" {
  for_each           = { for pair in setproduct(var.estimator_members, ["sandbox", "build"]) : "${pair[0]}|${pair[1]}" => pair }
  service_account_id = each.value[1] == "sandbox" ? google_service_account.sandbox.name : google_service_account.build.name
  role               = "roles/iam.serviceAccountUser"
  member             = each.value[0]
}

resource "google_storage_bucket_iam_member" "estimator_src" {
  for_each = toset(var.estimator_members)
  bucket   = google_storage_bucket.ml.name
  role     = "roles/storage.objectAdmin"
  member   = each.value
  condition {
    title      = "profiler-src only"
    expression = "resource.name.startsWith(\"${local.src_prefix}\")"
  }
}

resource "google_storage_bucket_iam_member" "estimator_models" {
  for_each = toset(var.estimator_members)
  bucket   = google_storage_bucket.ml.name
  role     = "roles/storage.objectViewer"
  member   = each.value
  condition {
    title      = "model registry read"
    expression = "resource.name.startsWith(\"${local.models_prefix}\")"
  }
}
