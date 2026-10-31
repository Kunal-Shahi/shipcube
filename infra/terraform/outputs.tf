output "service_accounts" {
  value = {
    api             = google_service_account.api.email
    gateway         = google_service_account.gateway.email
    worker          = google_service_account.worker.email
    hosted_deployer = google_service_account.hosted_deployer.email
    migrator        = google_service_account.migrator.email
    provisioner     = module.build_isolation.provisioner_sa
  }
}

output "sql_connection_name" {
  value = google_sql_database_instance.main.connection_name
}

output "apps_registry" {
  value = "${var.region}-docker.pkg.dev/${var.apps_project_id}/${google_artifact_registry_repository.apps.repository_id}"
}

output "budget_topic" {
  value = google_pubsub_topic.budget.id
}

output "profiler" {
  description = "Environment for the estimate worker (packages/profiler/deploy/RESOURCES.md)."
  value       = module.profiler.env
}

output "uploads_bucket" {
  value = google_storage_bucket.uploads.name
}

output "app_egress" {
  description = "Mediated egress for hosted apps (networking.md §5.5): SHIPCUBE_APP_EGRESS_* for the deployer, plus the shared NAT IP to show in the console."
  value = merge(module.app_egress.env, {
    nat_ip = module.app_egress.nat_ip
  })
}
