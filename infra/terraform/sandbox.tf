# Network for the profiler sandbox: Cloud Run jobs attach with Direct VPC egress
# (--network, --subnet, --vpc-egress=all-traffic), and every egress is denied.

resource "google_compute_network" "sandbox" {
  project                 = var.apps_project_id
  name                    = "sandbox"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.apps]
}

resource "google_compute_subnetwork" "sandbox" {
  project                  = var.apps_project_id
  name                     = "sandbox-${var.region}"
  region                   = var.region
  network                  = google_compute_network.sandbox.id
  ip_cidr_range            = "10.8.0.0/26"
  private_ip_google_access = false
}

resource "google_compute_firewall" "sandbox_deny_egress" {
  project            = var.apps_project_id
  name               = "sandbox-deny-all-egress"
  network            = google_compute_network.sandbox.name
  direction          = "EGRESS"
  priority           = 0
  destination_ranges = ["0.0.0.0/0"]
  deny {
    protocol = "all"
  }
}
