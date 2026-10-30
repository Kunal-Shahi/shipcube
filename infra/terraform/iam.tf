# Service accounts for the control plane. User apps get their own per-app
# accounts, created at deploy time by the deployer, with no project roles.

resource "google_service_account" "api" {
  project      = var.control_project_id
  account_id   = "sc-api"
  display_name = "ShipCube control-plane API and MCP server"
}

resource "google_service_account" "gateway" {
  project      = var.control_project_id
  account_id   = "sc-gateway"
  display_name = "ShipCube AI gateway and access proxy"
}

resource "google_service_account" "worker" {
  project      = var.control_project_id
  account_id   = "sc-worker"
  display_name = "Reconciler, usage roll-up, budget kill switch"
}

resource "google_service_account" "hosted_deployer" {
  project      = var.control_project_id
  account_id   = "sc-hosted-deployer"
  display_name = "Deploys hosted apps into the apps project"
}

# Cloud SQL IAM database auth for the services that talk to Postgres.
resource "google_project_iam_member" "sql_client" {
  for_each = {
    api    = google_service_account.api.member
    worker = google_service_account.worker.member
  }
  project = var.control_project_id
  role    = "roles/cloudsql.client"
  member  = each.value
}

resource "google_project_iam_member" "sql_instance_user" {
  for_each = {
    api    = google_service_account.api.member
    worker = google_service_account.worker.member
  }
  project = var.control_project_id
  role    = "roles/cloudsql.instanceUser"
  member  = each.value
}

# The API creates per-workspace deployer service accounts and per-secret IAM
# bindings in the control project.
resource "google_project_iam_member" "api_sa_admin" {
  project = var.control_project_id
  role    = "roles/iam.serviceAccountAdmin"
  member  = google_service_account.api.member
}

resource "google_project_iam_member" "api_secret_admin" {
  project = var.control_project_id
  role    = "roles/secretmanager.admin"
  member  = google_service_account.api.member
}

# The API impersonates deployer accounts to act on hosted and linked projects.
resource "google_service_account_iam_member" "api_impersonates_hosted_deployer" {
  service_account_id = google_service_account.hosted_deployer.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = google_service_account.api.member
}

# Hosted deployer gets the same grants users give it on linked projects
# (infra/scripts/link-project.sh). In the apps project it may also create and
# act as per-app service accounts, which hold no project roles.
resource "google_project_iam_custom_role" "run_iam" {
  project     = var.apps_project_id
  role_id     = "shipcubeRunIamAdmin"
  title       = "ShipCube Cloud Run IAM admin"
  description = "Only the setIamPolicy permissions missing from roles/run.developer."
  permissions = ["run.services.setIamPolicy", "run.jobs.setIamPolicy"]
}

resource "google_project_iam_member" "hosted_deployer" {
  for_each = toset([
    "roles/run.developer",
    "roles/iap.admin",
    "roles/cloudbuild.builds.editor",
    "roles/cloudscheduler.admin",
    "roles/logging.viewer",
    "roles/monitoring.viewer",
    "roles/serviceusage.serviceUsageConsumer",
    "roles/iam.serviceAccountAdmin",
    "roles/iam.serviceAccountUser",
    "projects/${var.apps_project_id}/roles/shipcubeRunIamAdmin",
  ])
  project    = var.apps_project_id
  role       = each.value
  member     = google_service_account.hosted_deployer.member
  depends_on = [google_project_iam_custom_role.run_iam]
}

# Kill switch: the worker may set max-instances=0 on hosted apps.
resource "google_project_iam_member" "worker_run_developer" {
  project = var.apps_project_id
  role    = "roles/run.developer"
  member  = google_service_account.worker.member
}

# Production deploys run in the worker (python -m shipcube_api.worker, as sc-worker), which
# impersonates the hosted deployer just like the API does.
resource "google_service_account_iam_member" "worker_impersonates_hosted_deployer" {
  service_account_id = google_service_account.hosted_deployer.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = google_service_account.worker.member
}

# Runs schema migrations (python -m shipcube_api.admin migrate) as the table owner.
resource "google_service_account" "migrator" {
  project      = var.control_project_id
  account_id   = "sc-migrator"
  display_name = "Schema migrations (owns the tables)"
}

resource "google_project_iam_member" "migrator_sql" {
  for_each = toset(["roles/cloudsql.client", "roles/cloudsql.instanceUser"])
  project  = var.control_project_id
  role     = each.value
  member   = google_service_account.migrator.member
}

# People and CI identities allowed to run migrations by impersonating sc-migrator.
resource "google_service_account_iam_member" "migrator_operators" {
  for_each           = toset(var.migration_operators)
  service_account_id = google_service_account.migrator.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = each.value
}

# Deploys pin the digest: the hosted deployer reads the pushed tag back from the workspace's
# repo (ws-<ws_short>). Read-only; builds push as the per-workspace build SA.
resource "google_project_iam_member" "hosted_deployer_ar_reader" {
  project = var.apps_project_id
  role    = "roles/artifactregistry.reader"
  member  = google_service_account.hosted_deployer.member
}
