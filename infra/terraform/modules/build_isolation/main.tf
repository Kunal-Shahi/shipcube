# Per-workspace build isolation (red-team C-1).
#
# Each workspace builds as its own service account, which can read only its own uploads
# folder and push only to its own Artifact Registry repo:
#
#   SA      sc-build-<ws_short>@<apps project>.iam.gserviceaccount.com
#   uploads gs://<uploads bucket>/ws/<ws_uuid>/      (managed folder, objectViewer)
#   logs    gs://<uploads bucket>/ws/<ws_uuid>/build-logs/   (managed folder, objectUser)
#   images  <region>-docker.pkg.dev/<apps project>/ws-<ws_short>/   (repo, artifactregistry.writer)
#
# ws_uuid is the canonical workspace uuid; ws_short is its first 20 hex digits. Those
# per-workspace resources are created at runtime by the worker, impersonating the
# provisioner SA defined here (reference: infra/scripts/provision-workspace-builder.sh).
# This module holds only the shared, static pieces.

resource "google_service_account" "provisioner" {
  project      = var.control_project_id
  account_id   = "sc-provisioner"
  display_name = "Creates per-workspace build identities, repos and upload folders"
}

# Only the worker may act as the provisioner.
resource "google_service_account_iam_member" "worker_impersonates_provisioner" {
  service_account_id = google_service_account.provisioner.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = var.worker_member
}

# Neither IAM service accounts nor Artifact Registry repos support resource-name conditions,
# so this cannot be narrowed to sc-build-* / ws-*. It is no broader than the hosted deployer's
# existing roles/iam.serviceAccountAdmin in the apps project.
resource "google_project_iam_custom_role" "provisioner" {
  project     = var.apps_project_id
  role_id     = "shipcubeBuildProvisioner"
  title       = "ShipCube build provisioner"
  description = "Create and delete per-workspace build SAs and Artifact Registry repos, and set their IAM."
  permissions = [
    "iam.serviceAccounts.create",
    "iam.serviceAccounts.delete",
    "iam.serviceAccounts.get",
    "iam.serviceAccounts.getIamPolicy",
    "iam.serviceAccounts.setIamPolicy",
    "artifactregistry.repositories.create",
    "artifactregistry.repositories.delete",
    "artifactregistry.repositories.get",
    "artifactregistry.repositories.getIamPolicy",
    "artifactregistry.repositories.setIamPolicy",
    "artifactregistry.repositories.update",
  ]
}

resource "google_project_iam_member" "provisioner" {
  project = var.apps_project_id
  role    = google_project_iam_custom_role.provisioner.id
  member  = google_service_account.provisioner.member
}

# Managed folders on the uploads bucket only (granted on the bucket, not the project).
resource "google_project_iam_custom_role" "folders" {
  project     = var.apps_project_id
  role_id     = "shipcubeUploadFolderAdmin"
  title       = "ShipCube upload folder admin"
  description = "Create and delete managed folders and set their IAM."
  permissions = [
    "storage.managedFolders.create",
    "storage.managedFolders.delete",
    "storage.managedFolders.get",
    "storage.managedFolders.list",
    "storage.managedFolders.getIamPolicy",
    "storage.managedFolders.setIamPolicy",
  ]
}

resource "google_storage_bucket_iam_member" "provisioner_folders" {
  bucket = var.uploads_bucket
  role   = google_project_iam_custom_role.folders.id
  member = google_service_account.provisioner.member
}

# No logging.logWriter for builders (red-team #28: granting it to every SA in the apps project
# let tenant apps forge other tenants' log entries). Hosted builds use GCS_ONLY logging into
# gs://<uploads bucket>/ws/<ws_uuid>/build-logs/, a managed folder only that workspace's builder
# can write (objectUser), created with the upload folder by the provisioner.
