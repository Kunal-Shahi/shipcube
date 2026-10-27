# Secret containers only. Values are added by hand or by the API, never in Terraform
# (Terraform state would hold them in plain text).

locals {
  platform_secrets = {
    "meta-api-key"      = google_service_account.gateway.member
    "token-hmac-pepper" = google_service_account.api.member
  }
}

resource "google_secret_manager_secret" "platform" {
  for_each  = local.platform_secrets
  project   = var.control_project_id
  secret_id = each.key
  replication {
    auto {}
  }
  depends_on = [google_project_service.control]
}

resource "google_secret_manager_secret_iam_member" "platform_accessor" {
  for_each  = local.platform_secrets
  project   = var.control_project_id
  secret_id = google_secret_manager_secret.platform[each.key].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = each.value
}
