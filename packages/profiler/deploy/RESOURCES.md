# Profiler sandbox and ML bucket: required resources

These resources are managed as infrastructure as code by the infra/security workstream (`infra/**`). Don't create them by hand. Region: `asia-south1`. Project ids are configuration (env vars), not constants.

Kunal's decision (Oct 27):
- The profiler sandbox lives in **`your-apps-project`**.
- The ML bucket **`your-ml-bucket`** stays in **`your-control-project`**.

## In `your-apps-project` (the sandbox)

| # | Resource | Settings |
|---|---|---|
| 1 | APIs | run, cloudbuild, artifactregistry, logging, compute, iam |
| 2 | VPC `profiler-no-egress` | custom subnet mode |
| 3 | Subnet `profiler` | `10.80.0.0/24`, `private_ip_google_access = false` |
| 4 | Firewall `profiler-no-egress-deny-egress` | EGRESS, DENY all, `0.0.0.0/0`, priority 100, no allow rules |
| 5 | SA `profiler-sandbox` | **zero roles**, no secrets: the Cloud Run job's runtime identity |
| 6 | SA `profiler-build` | Cloud Build identity (instead of the default compute SA) |
| 7 | Artifact Registry `profiler` (Docker) | cleanup policy: delete images older than 7 days (`604800s`) |

## In `your-control-project`

| # | Resource | Settings |
|---|---|---|
| 8 | Bucket `your-ml-bucket` | uniform bucket-level access, public access prevention enforced, lifecycle: Delete when age ≥ 1 day and `matchesPrefix: ["profiler-src/"]` |

Bucket layout:
- `models/workload-classifier/<version>/…` and `latest.json`, kept (the model registry)
- `profiler-src/<run id>.tar.gz`, expires after 1 day

## IAM (least privilege)

`profiler-build@your-apps-project` needs:
- `roles/artifactregistry.writer` on repo `profiler`
- `roles/logging.logWriter` on `your-apps-project`
- `roles/storage.objectViewer` on `gs://your-ml-bucket`, with the condition `resource.name.startsWith("projects/_/buckets/your-ml-bucket/objects/profiler-src/")`

The **caller** is the service account that runs `estimate()` (the API worker). It needs:
- in `your-apps-project`:
  - `roles/run.developer`
  - `roles/cloudbuild.builds.editor`
  - `roles/logging.viewer`
  - `roles/iam.serviceAccountUser` on `profiler-sandbox` and on `profiler-build`
  - `roles/serviceusage.serviceUsageConsumer`: the client bills API quota to this project (`x-goog-user-project`). Without it, user credentials billed Cloud Logging reads to gcloud's shared client project, which returned 429.
- on `gs://your-ml-bucket`:
  - `roles/storage.objectAdmin`, with the condition prefix `profiler-src/` (it uploads and deletes tarballs)
  - `roles/storage.objectViewer`, with the condition prefix `models/workload-classifier/` (model registry reads)

The publisher, whoever runs `scripts/publish_model.py` (a person or a CI job), needs `roles/storage.objectCreator` on the prefix `models/workload-classifier/`. Published versions are immutable (`ifGenerationMatch=0`); only `latest.json` is overwritten, which also needs `objectAdmin` on that one object.

`profiler-sandbox` gets **no** grants. The job image contains the code, so it reads nothing from the bucket.

## Not in Terraform: created per estimate by `CloudRunJobRunner`

Each estimate creates a Cloud Run v2 job named `prof-<estimate id>` and **deletes it after the run**. Its settings:
- execution environment gen2
- `serviceAccount: profiler-sandbox`
- `vpcAccess: {egress: ALL_TRAFFIC, networkInterfaces: [{network: profiler-no-egress, subnetwork: profiler}]}`
- 1 vCPU / 2 GiB, `timeout: 600s`, `maxRetries: 0`, no secrets

Each run also produces a Cloud Build build and an image, which the cleanup policy expires.

## Outputs the worker needs (environment)

```
SHIPCUBE_PROFILER_PROJECT=your-apps-project
SHIPCUBE_PROFILER_REGION=asia-south1
SHIPCUBE_PROFILER_BUCKET=your-ml-bucket
SHIPCUBE_PROFILER_REPOSITORY=profiler
SHIPCUBE_PROFILER_NETWORK=profiler-no-egress
SHIPCUBE_PROFILER_SUBNET=profiler
SHIPCUBE_PROFILER_SERVICE_ACCOUNT=profiler-sandbox@your-apps-project.iam.gserviceaccount.com
SHIPCUBE_PROFILER_BUILD_SERVICE_ACCOUNT=profiler-build@your-apps-project.iam.gserviceaccount.com
SHIPCUBE_MODEL_URI=gs://your-ml-bucket/models/workload-classifier/latest
```

`your-apps-project` is a placeholder for the real project id. The job and build run in `SHIPCUBE_PROFILER_PROJECT`; the bucket can be in another project.
