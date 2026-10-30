# Link deployers (security-tenancy §3) live in their own project, so the API's power over them
# can't reach the control plane's own service accounts (#15, H-1).
#
# The API creates ws-<id>-deployer here when a workspace starts linking, then checks links by
# impersonating it; the worker deploys to linked projects the same way. Every SA in this project
# is a link deployer, so project-level grants are exactly "these accounts": the API can create
# them (not delete them or change their IAM), and the API and worker can act as them. They hold
# no roles of their own; users' grants in their own projects are all they can use.
#
# No billing account: only the IAM APIs run here, and they don't need one.
resource "google_project" "links" {
  project_id      = var.links_project_id
  name            = "ShipCube link deployers"
  deletion_policy = "PREVENT"
  labels          = { owner = "shipcube", role = "links" }
}

resource "google_project_service" "links" {
  for_each           = toset(["iam.googleapis.com", "iamcredentials.googleapis.com"])
  project            = google_project.links.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_project_iam_member" "api_creates_link_deployers" {
  project    = google_project.links.project_id
  role       = "roles/iam.serviceAccountCreator"
  member     = google_service_account.api.member
  depends_on = [google_project_service.links]
}

resource "google_project_iam_member" "link_deployer_users" {
  for_each = {
    api    = google_service_account.api.member
    worker = google_service_account.worker.member
  }
  project    = google_project.links.project_id
  role       = "roles/iam.serviceAccountTokenCreator"
  member     = each.value
  depends_on = [google_project_service.links]
}
