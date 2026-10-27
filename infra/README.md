# infra

Infrastructure as code for ShipCube's own GCP projects, plus the scripts users run to link their own project. Design and reasoning: [docs/research/security-tenancy.md](../docs/research/security-tenancy.md).

**Nothing here has been applied to a real account yet.** It is a skeleton to review.

| Path | What it is |
|---|---|
| `terraform/` | ShipCube's two projects: `shipcube-control` (API, MCP, gateway, Cloud SQL, secrets) and `shipcube-apps` (hosted apps, profiler sandbox) |
| `scripts/link-project.sh` | Run by a user, as Owner of their project, to let their workspace's ShipCube deployer deploy there |
| `scripts/unlink-project.sh` | Removes every grant `link-project.sh` made |

## Terraform

The projects themselves are created by hand (a free-trial account has no organization to create them under). Then:

```bash
gcloud storage buckets create gs://<control-project>-tfstate --location=asia-south1 --uniform-bucket-level-access
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars   # fill in project IDs and billing account; gitignored
terraform init -backend-config="bucket=<control-project>-tfstate"
terraform plan
```

What it creates:

- APIs in both projects.
- Service accounts: `sc-api`, `sc-gateway`, `sc-worker`, `sc-hosted-deployer` (control), `sc-profiler`, `sc-builder` (apps). Per-workspace deployers (`ws-<id>-deployer`) and per-app runtime accounts are created by the API at runtime, not here.
- Cloud SQL Postgres 16, `db-f1-micro`, IAM database auth only.
- Secret Manager **containers** for platform secrets, with accessor bindings. Values are added with `gcloud secrets versions add`, never through Terraform (state would hold them in plain text).
- Artifact Registry repos, with a cleanup policy for app images.
- The profiler sandbox VPC with a deny-all egress firewall.
- A billing budget on the credit pool that publishes to Pub/Sub for the kill switch.

Manual steps Terraform can't do:

1. Enable Identity Platform and the Google provider in the console.
2. Create the external OAuth client for IAP in `shipcube-apps` (the IAP OAuth Admin API is shut down for new projects).
3. Add secret values: `meta-api-key`, `token-hmac-pepper`, `oauth-client-secret`.
