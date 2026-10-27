locals {
  control_apis = [
    "run.googleapis.com",
    "sqladmin.googleapis.com",
    "secretmanager.googleapis.com",
    "identitytoolkit.googleapis.com", # Identity Platform
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
    "pubsub.googleapis.com",
    "billingbudgets.googleapis.com",
    "cloudresourcemanager.googleapis.com",
  ]
  apps_apis = [
    "run.googleapis.com",
    "iap.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "secretmanager.googleapis.com",
    "cloudscheduler.googleapis.com",
    "compute.googleapis.com", # VPC for the profiler sandbox
  ]
}

resource "google_project_service" "control" {
  for_each           = toset(local.control_apis)
  project            = var.control_project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_project_service" "apps" {
  for_each           = toset(local.apps_apis)
  project            = var.apps_project_id
  service            = each.value
  disable_on_destroy = false
}
