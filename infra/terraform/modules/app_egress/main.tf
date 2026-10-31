# Mediated egress for deployed hosted apps (networking.md §5.5, red-team N-2).
#
# Without this, a Cloud Run service has unrestricted direct outbound internet, so a tenant app can be
# used as a malicious egress origin (stratum mining pools on odd ports, port-25 spam relay, C2 on
# arbitrary ports, bulk exfiltration). CPU/instance/spend caps bound mining but not egress abuse.
#
# Every hosted app attaches to this VPC with Direct VPC egress `all-traffic` (deploy/gcp.py
# `_service_body`), so ALL of its outbound traffic is subject to the egress firewall below: TCP 443
# and 80 to the internet and the metadata server are allowed, everything else is denied. Ordinary
# HTTPS calls to business APIs keep working; raw TCP on other ports does not. Cloud NAT gives the
# subnet (which has no external IPs) a route to the internet, through one reserved IP the console can
# show for customer-DB allow-listing (the `egress: static` tier shares this same NAT).
#
# Contrast with modules/profiler/main.tf, which denies ALL egress (profiling runs untrusted code with
# no reason to reach out). Here we allow-HTTPS instead of deny-all, because deployed apps legitimately
# call APIs.
#
# Residual, NOT closed here (documented follow-up, networking.md §5.5): a port-only filter still passes
# TLS-tunnelled abuse on 443 (mining pools and HTTPS exfil over 443). Closing that needs an
# SNI/domain-filtering egress proxy. Linked projects (the customer's own GCP project) need their own
# NAT and firewall; this module only covers the hosted apps project.

resource "google_compute_network" "egress" {
  project                 = var.project_id
  name                    = var.network_name
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "egress" {
  project       = var.project_id
  name          = var.subnet_name
  region        = var.region
  network       = google_compute_network.egress.id
  ip_cidr_range = var.subnet_cidr
  # Reach Google APIs over the restricted VIP without needing a public NAT hop.
  private_ip_google_access = true
}

# --- Egress firewall: allow HTTPS/HTTP, deny the rest ------------------------

resource "google_compute_firewall" "allow_metadata" {
  project            = var.project_id
  name               = "${var.network_name}-allow-metadata"
  network            = google_compute_network.egress.name
  direction          = "EGRESS"
  priority           = 900
  destination_ranges = ["169.254.169.254/32"]
  allow {
    protocol = "tcp"
  }
  allow {
    protocol = "udp"
    ports    = ["53"]
  }
}

resource "google_compute_firewall" "allow_web" {
  project            = var.project_id
  name               = "${var.network_name}-allow-web"
  network            = google_compute_network.egress.name
  direction          = "EGRESS"
  priority           = 1000
  destination_ranges = ["0.0.0.0/0"]
  allow {
    protocol = "tcp"
    ports    = var.allowed_tcp_ports
  }
}

resource "google_compute_firewall" "deny_egress" {
  project            = var.project_id
  name               = "${var.network_name}-deny-egress"
  network            = google_compute_network.egress.name
  direction          = "EGRESS"
  priority           = 1100
  destination_ranges = ["0.0.0.0/0"]
  deny {
    protocol = "all"
  }
}

# --- Cloud NAT: the subnet has no external IPs, so give it one outbound route -----

resource "google_compute_router" "egress" {
  project = var.project_id
  name    = "${var.network_name}-router"
  region  = var.region
  network = google_compute_network.egress.id
}

# A reserved IP so the console can show a stable address for customer-DB allow-listing. The IP is
# shared across all hosted apps with egress (networking.md §5.5): tenants who need isolation use a
# linked project.
resource "google_compute_address" "nat" {
  project      = var.project_id
  name         = "${var.network_name}-nat"
  region       = var.region
  address_type = "EXTERNAL"
}

resource "google_compute_router_nat" "egress" {
  project                            = var.project_id
  name                               = "${var.network_name}-nat"
  router                             = google_compute_router.egress.name
  region                             = var.region
  nat_ip_allocate_option             = "MANUAL_ONLY"
  nat_ips                            = [google_compute_address.nat.self_link]
  source_subnetwork_ip_ranges_to_nat = "LIST_OF_SUBNETWORKS"

  subnetwork {
    name                    = google_compute_subnetwork.egress.id
    source_ip_ranges_to_nat = ["ALL_IP_RANGES"]
  }

  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}
