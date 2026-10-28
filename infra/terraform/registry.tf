resource "google_artifact_registry_repository" "apps" {
  project       = var.apps_project_id
  location      = var.region
  repository_id = "shipcube"
  format        = "DOCKER"
  description   = "Images for hosted user apps"

  # Images must outlive the 7-day soft-delete window (restore redeploys the same image),
  # so delete only after 30 days, and always keep the 5 newest versions of each app.
  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 5
    }
  }
  cleanup_policies {
    id     = "delete-old"
    action = "DELETE"
    condition {
      older_than = "2592000s" # 30 days
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

  # Keep the 5 newest images per service (rollback targets); delete the rest after 30 days.
  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 5
    }
  }
  cleanup_policies {
    id     = "delete-old"
    action = "DELETE"
    condition {
      older_than = "2592000s" # 30 days
    }
  }
  depends_on = [google_project_service.control]
}
