# Plan-only test of the profiler sandbox with a mocked provider: no credentials, no GCP
# calls. Run: terraform -chdir=infra/terraform/modules/profiler init -backend=false &&
#             terraform -chdir=infra/terraform/modules/profiler test
# infra/tests/profiler_sandbox_check.py covers what a module test can't see: that nothing
# elsewhere in infra/terraform opens a path out or grants the sandbox SA a role.

mock_provider "google" {}

variables {
  project_id        = "shipcube-apps-test"
  region            = "asia-south1"
  ml_bucket_project = "shipcube-control-test"
  ml_bucket_name    = "shipcube-test-ml"
  estimator_members = ["serviceAccount:sc-worker@shipcube-control-test.iam.gserviceaccount.com"]
}

run "no_egress_path" {
  command = plan

  assert {
    condition     = google_compute_network.sandbox.auto_create_subnetworks == false
    error_message = "the sandbox network must not auto-create subnets"
  }
  assert {
    condition     = google_compute_subnetwork.sandbox.private_ip_google_access == false
    error_message = "Private Google Access would let the sandbox reach Google APIs"
  }
  assert {
    condition = (
      google_compute_firewall.deny_egress.direction == "EGRESS" &&
      google_compute_firewall.deny_egress.destination_ranges == toset(["0.0.0.0/0"]) &&
      length(google_compute_firewall.deny_egress.allow) == 0 &&
      length(google_compute_firewall.deny_egress.deny) == 1 &&
      one(google_compute_firewall.deny_egress.deny).protocol == "all" &&
      google_compute_firewall.deny_egress.disabled != true
    )
    error_message = "the sandbox firewall must deny all egress to 0.0.0.0/0 and allow nothing"
  }
}

run "sandbox_sa_has_zero_bindings" {
  command = plan

  assert {
    condition     = google_service_account.sandbox.account_id == "profiler-sandbox"
    error_message = "the sandbox runtime SA is profiler-sandbox (env SHIPCUBE_PROFILER_SERVICE_ACCOUNT)"
  }
  # The only IAM on the sandbox SA is actAs for the estimator, so it can launch jobs as it.
  assert {
    condition = alltrue([
      for k, b in google_service_account_iam_member.estimator_acts_as :
      b.role == "roles/iam.serviceAccountUser" && !strcontains(b.member, "profiler-sandbox")
    ])
    error_message = "only roles/iam.serviceAccountUser for the estimator may be bound on the sandbox SA"
  }
  assert {
    condition = alltrue(concat(
      [for k, b in google_project_iam_member.estimator : !strcontains(b.member, "profiler-sandbox")],
      [for k, b in google_storage_bucket_iam_member.estimator_src : !strcontains(b.member, "profiler-sandbox")],
      [for k, b in google_storage_bucket_iam_member.estimator_models : !strcontains(b.member, "profiler-sandbox")],
    ))
    error_message = "the sandbox SA must not be a member of any binding"
  }
}
