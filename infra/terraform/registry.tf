resource "google_artifact_registry_repository" "apps" {
  project       = var.apps_project_id
  location      = var.region
  repository_id = "shipcube"
  format        = "DOCKER"
  description   = "Images for hosted user apps"

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 5
    }
  }
  depends_on = [google_project_service.apps]
}

resource "google_artifact_registry_repository" "platform" {
  project       = var.control_project_id
  location      = var.region
  repository_id = "platform"
  format        = "DOCKER"
  description   = "Images for ShipCube's own services"
  depends_on    = [google_project_service.control]
}
