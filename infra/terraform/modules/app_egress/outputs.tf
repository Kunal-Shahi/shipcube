output "network" {
  value = google_compute_network.egress.name
}

output "subnet" {
  value = google_compute_subnetwork.egress.name
}

output "nat_ip" {
  description = "The shared egress IP hosted apps leave through; show it in the console for customer-DB allow-listing."
  value       = google_compute_address.nat.address
}

output "env" {
  description = "SHIPCUBE_APP_EGRESS_* settings for the deployer (worker). Attached to every hosted app's Cloud Run service."
  value = {
    SHIPCUBE_APP_EGRESS_NETWORK = google_compute_network.egress.name
    SHIPCUBE_APP_EGRESS_SUBNET  = google_compute_subnetwork.egress.name
  }
}
