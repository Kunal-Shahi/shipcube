terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.50"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.7"
    }
  }
  # State lives in a GCS bucket created by hand once (see infra/README.md).
  backend "gcs" {}
}

provider "google" {
  region = var.region
  # Billing Budgets and some other APIs need a quota project when called with user credentials.
  billing_project       = var.control_project_id
  user_project_override = true
}
