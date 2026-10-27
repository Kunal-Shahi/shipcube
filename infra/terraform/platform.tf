# Hosting ShipCube's own services on Cloud Run (platform workstream; deploy/README.md).
# The services themselves are deployed by deploy/deploy.sh. This file holds the identities, grants,
# secrets and build bucket they need, so all IAM stays in Terraform.

# --- Console ------------------------------------------------------------------------------

resource "google_service_account" "web" {
  project      = var.control_project_id
  account_id   = "sc-web"
  display_name = "ShipCube web console"
}

# Verify ID tokens, mint and verify session cookies, revoke on sign-out (apps/web/README.md).
resource "google_project_iam_member" "web_firebaseauth" {
  project = var.control_project_id
  role    = "roles/firebaseauth.admin"
  member  = google_service_account.web.member
}

# --- Builds of ShipCube's own images ---------------------------------------------------------

resource "google_service_account" "platform_build" {
  project      = var.control_project_id
  account_id   = "sc-platform-build"
  display_name = "Cloud Build for ShipCube's own images"
}

resource "google_artifact_registry_repository_iam_member" "platform_build_writer" {
  project    = var.control_project_id
  location   = var.region
  repository = google_artifact_registry_repository.platform.repository_id
  role       = "roles/artifactregistry.writer"
  member     = google_service_account.platform_build.member
}

resource "google_project_iam_member" "platform_build_logs" {
  project = var.control_project_id
  role    = "roles/logging.logWriter"
  member  = google_service_account.platform_build.member
}

# Source tarballs for `gcloud builds submit`. Only the build account reads them; kept 7 days.
resource "google_storage_bucket" "platform_builds" {
  project                     = var.control_project_id
  name                        = "${var.control_project_id}-platform-builds"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true

  lifecycle_rule {
    condition {
      age = 7
    }
    action {
      type = "Delete"
    }
  }
}

resource "google_storage_bucket_iam_member" "platform_build_source" {
  bucket = google_storage_bucket.platform_builds.name
  role   = "roles/storage.objectViewer"
  member = google_service_account.platform_build.member
}

# --- Control-plane API (sc-api) --------------------------------------------------------------

# Signed upload URLs: the API signs as itself through IAM signBlob (settings.signer_sa).
resource "google_service_account_iam_member" "api_signs_as_itself" {
  service_account_id = google_service_account.api.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = google_service_account.api.member
}

# inspect_app reads hosted apps' metrics (packages/monitor). logging.viewer is already granted
# through the profiler module's estimator roles.
resource "google_project_iam_member" "api_monitoring_viewer" {
  project = var.apps_project_id
  role    = "roles/monitoring.viewer"
  member  = google_service_account.api.member
}

# --- Worker (sc-worker) ----------------------------------------------------------------------

# estimate_load runs in the worker; Muse Spark writes its reasoning.
resource "google_secret_manager_secret_iam_member" "worker_meta_key" {
  project   = var.control_project_id
  secret_id = google_secret_manager_secret.platform["meta-api-key"].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.worker.member
}

# --- AI gateway (sc-gateway) -----------------------------------------------------------------

# The gateway's app registry, until the API serves /internal/gateway/resolve. Values are written by
# deploy/gateway_registry.py (service account -> app), never by Terraform.
resource "google_secret_manager_secret" "gateway_registry" {
  project   = var.control_project_id
  secret_id = "gateway-registry"
  replication {
    auto {}
  }
  depends_on = [google_project_service.control]
}

resource "google_secret_manager_secret_iam_member" "gateway_registry_accessor" {
  project   = var.control_project_id
  secret_id = google_secret_manager_secret.gateway_registry.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.gateway.member
}

output "platform" {
  value = {
    web_service_account   = google_service_account.web.email
    build_service_account = google_service_account.platform_build.email
    build_bucket          = google_storage_bucket.platform_builds.name
  }
}

# Calls the API and worker make as themselves against the apps project (upload bucket reads and
# writes, inspect_app's Monitoring/Logging reads, the profiler) bill their quota to that project,
# which needs serviceusage.services.use there. Deploys themselves impersonate sc-hosted-deployer.
resource "google_project_iam_member" "apps_service_usage" {
  for_each = {
    api    = google_service_account.api.member
    worker = google_service_account.worker.member
  }
  project = var.apps_project_id
  role    = "roles/serviceusage.serviceUsageConsumer"
  member  = each.value
}
