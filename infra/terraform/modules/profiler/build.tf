# Phase 2 build isolation for the profiler (red-team P-1, #22; docs/research/profiler-sandbox.md §4).
#
# Off by default (var.offline_builds): nothing here exists until it's switched on, and
# applying it needs Kunal's OK (a private pool and mirrors are billable).
#
# - Each profiler build runs as the workspace's own builder SA (sc-build-<ws>, from the
#   build_isolation module), reads its source from the workspace's uploads folder and
#   pushes to the workspace's repo. CloudRunJobRunner takes those per run.
# - Wheels come from an Artifact Registry remote repository mirroring PyPI, and the base
#   image from one mirroring Docker Hub, so builds need no internet.
# - Builds run on a private pool whose workers have no external IP, peered to a VPC with
#   no NAT or router: no path to the internet at all. The docker build itself also runs
#   with --network=none; only the fetch step talks to the mirrors.

resource "google_artifact_registry_repository" "pypi_remote" {
  count         = var.offline_builds ? 1 : 0
  project       = var.project_id
  location      = var.region
  repository_id = "pypi-remote"
  format        = "PYTHON"
  mode          = "REMOTE_REPOSITORY"
  description   = "PyPI mirror for offline profiler builds"

  remote_repository_config {
    description = "pypi.org"
    python_repository {
      public_repository = "PYPI"
    }
  }

  cleanup_policies {
    id     = "delete-after-30-days"
    action = "DELETE"
    condition {
      older_than = "2592000s"
    }
  }
}

resource "google_artifact_registry_repository" "dockerhub_remote" {
  count         = var.offline_builds ? 1 : 0
  project       = var.project_id
  location      = var.region
  repository_id = "dockerhub-remote"
  format        = "DOCKER"
  mode          = "REMOTE_REPOSITORY"
  description   = "Docker Hub mirror for the profiler's base image"

  remote_repository_config {
    description = "Docker Hub"
    docker_repository {
      public_repository = "DOCKER_HUB"
    }
  }
}

# The private pool's VPC: peered with Google's service network, and nothing else. No
# router, no NAT, no external IPs (worker_config.no_external_ip), so no internet.
resource "google_compute_network" "build" {
  count                   = var.offline_builds ? 1 : 0
  project                 = var.project_id
  name                    = "profiler-build"
  auto_create_subnetworks = false
}

resource "google_compute_global_address" "build_peering" {
  count         = var.offline_builds ? 1 : 0
  project       = var.project_id
  name          = "profiler-build-peering"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 24
  network       = google_compute_network.build[0].id
}

resource "google_service_networking_connection" "build" {
  count                   = var.offline_builds ? 1 : 0
  network                 = google_compute_network.build[0].id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.build_peering[0].name]
}

resource "google_cloudbuild_worker_pool" "profiler" {
  count    = var.offline_builds ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = "profiler"

  worker_config {
    machine_type   = "e2-standard-2"
    disk_size_gb   = 100
    no_external_ip = true
  }

  network_config {
    peered_network = google_compute_network.build[0].id
  }

  depends_on = [google_service_networking_connection.build]
}

# The shared build SA (kept for the integration test) reads the mirrors. Per-workspace
# builders get the same grant when they're provisioned (infra/scripts/provision-workspace-builder.sh
# and the worker's ensure_builder): SAs created at runtime can't be listed here.
resource "google_artifact_registry_repository_iam_member" "build_reads_mirrors" {
  for_each   = var.offline_builds ? toset(["pypi-remote", "dockerhub-remote"]) : toset([])
  project    = var.project_id
  location   = var.region
  repository = each.value
  role       = "roles/artifactregistry.reader"
  member     = google_service_account.build.member
  depends_on = [google_artifact_registry_repository.pypi_remote, google_artifact_registry_repository.dockerhub_remote]
}

# Whoever submits profiler builds (the API and the worker) may run them on the pool.
resource "google_project_iam_member" "estimator_uses_pool" {
  for_each = var.offline_builds ? toset(var.estimator_members) : toset([])
  project  = var.project_id
  role     = "roles/cloudbuild.workerPoolUser"
  member   = each.value
}

locals {
  offline_env = var.offline_builds ? {
    SHIPCUBE_PROFILER_WORKER_POOL = google_cloudbuild_worker_pool.profiler[0].id
    SHIPCUBE_PROFILER_PIP_INDEX   = "https://${var.region}-python.pkg.dev/${var.project_id}/pypi-remote/simple/"
    # Pin it: append @sha256:<digest> of python:3.12-slim once mirrored.
    SHIPCUBE_PROFILER_BASE_IMAGE = "${var.region}-docker.pkg.dev/${var.project_id}/dockerhub-remote/library/python:3.12-slim"
  } : {}
}
