terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
  # State lives in a GCS bucket created by hand once (see infra/README.md).
  backend "gcs" {}
}

provider "google" {
  region = var.region
}
