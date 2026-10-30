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

# Workspace secrets (BYOK AI keys, warehouse credentials): ws-<workspace>-<kind>-<uuid>, created by
# the API (secret_store.py), read by the gateway (AI keys) and the data layer in the API (warehouse
# credentials). IAM conditions on Secret Manager match the resource name, which uses the project
# number: projects/<number>/secrets/<id>[/versions/<v>]. Creating a secret is checked against the
# project, not the new name, so `create` is a separate unconditional grant; a secret created under
# any other name is unusable to the API (no versions, no reads).
locals {
  workspace_secrets_prefix = "projects/${data.google_project.control.number}/secrets/ws-"
  # AI keys only: ws-ai-<workspace>-<uuid>, the canonical name (the API creates it and the gateway
  # checks it; #61). Warehouse credentials are ws-<workspace>-warehouse_credential-<uuid>; a
  # workspace uuid is hex, so never "ai-".
  ai_key_secrets_prefix = "projects/${data.google_project.control.number}/secrets/ws-ai-"
}

resource "google_project_iam_custom_role" "secret_creator" {
  project     = var.control_project_id
  role_id     = "shipcubeSecretCreator"
  title       = "ShipCube secret creator"
  description = "Create secrets; everything else on them comes from a name-conditioned role."
  permissions = ["secretmanager.secrets.create"]
}

resource "google_project_iam_custom_role" "workspace_secrets" {
  project     = var.control_project_id
  role_id     = "shipcubeWorkspaceSecrets"
  title       = "ShipCube workspace secrets"
  description = "Add, read and destroy versions of workspace secrets (granted on ws-* only)."
  permissions = [
    "secretmanager.secrets.get",
    "secretmanager.versions.add",
    "secretmanager.versions.access",
    "secretmanager.versions.destroy",
    "secretmanager.versions.get",
  ]
}

resource "google_project_iam_member" "api_creates_secrets" {
  project = var.control_project_id
  role    = google_project_iam_custom_role.secret_creator.id
  member  = google_service_account.api.member
}

resource "google_project_iam_member" "api_workspace_secrets" {
  project = var.control_project_id
  role    = google_project_iam_custom_role.workspace_secrets.id
  member  = google_service_account.api.member
  condition {
    title       = "workspace secrets only"
    description = "ws-<workspace>-* secrets and their versions"
    expression  = "resource.name.startsWith(\"${local.workspace_secrets_prefix}\")"
  }
}

# The gateway reads a workspace's AI key by reference at request time, and nothing else: not
# warehouse credentials, which share the ws- prefix.
resource "google_project_iam_member" "gateway_ai_keys" {
  project = var.control_project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = google_service_account.gateway.member
  condition {
    title       = "workspace AI keys only"
    description = "ws-ai-<workspace>-* secrets and their versions"
    expression  = "resource.name.startsWith(\"${local.ai_key_secrets_prefix}\")"
  }
}
