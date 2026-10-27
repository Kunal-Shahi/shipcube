output "service_accounts" {
  value = {
    api             = google_service_account.api.email
    gateway         = google_service_account.gateway.email
    worker          = google_service_account.worker.email
    hosted_deployer = google_service_account.hosted_deployer.email
    profiler        = google_service_account.profiler.email
    builder         = google_service_account.builder.email
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
