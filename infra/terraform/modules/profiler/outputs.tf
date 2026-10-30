output "env" {
  description = "SHIPCUBE_PROFILER_* settings for the estimate worker."
  value = merge(local.offline_env, {
    SHIPCUBE_PROFILER_PROJECT               = var.project_id
    SHIPCUBE_PROFILER_REGION                = var.region
    SHIPCUBE_PROFILER_BUCKET                = google_storage_bucket.ml.name
    SHIPCUBE_PROFILER_REPOSITORY            = google_artifact_registry_repository.profiler.repository_id
    SHIPCUBE_PROFILER_NETWORK               = google_compute_network.sandbox.name
    SHIPCUBE_PROFILER_SUBNET                = google_compute_subnetwork.sandbox.name
    SHIPCUBE_PROFILER_SERVICE_ACCOUNT       = google_service_account.sandbox.email
    SHIPCUBE_PROFILER_BUILD_SERVICE_ACCOUNT = google_service_account.build.email
    SHIPCUBE_MODEL_URI                      = "gs://${google_storage_bucket.ml.name}/models/workload-classifier/latest"
  })
}
