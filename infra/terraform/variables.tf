variable "control_project_id" {
  description = "Project for the control plane: API, MCP server, gateway, Cloud SQL, platform secrets."
  type        = string
}

variable "apps_project_id" {
  description = "Project where hosted user apps and the profiler sandbox run."
  type        = string
}

variable "billing_account_id" {
  description = "Billing account (free-trial credits) used by both projects. Needed for the budget."
  type        = string
}

variable "region" {
  type    = string
  default = "asia-south1"
}

variable "budget_amount" {
  description = "Hard cap on gross spend (before credits) for the whole budget period, in budget_currency. The kill switch unlinks billing at this amount."
  type        = number
  default     = 24000
}

variable "budget_period" {
  description = "One period covering the whole free trial, so the cap is cumulative rather than monthly."
  type        = object({ start = string, end = string }) # YYYY-MM-DD
  default     = { start = "2026-10-01", end = "2027-01-31" }
}

variable "killswitch_projects" {
  description = "Every project billed to the account, in shutdown order; the control project (which hosts the kill switch) must be last."
  type        = list(string)
}

variable "budget_currency" {
  description = "Must match the billing account's currency."
  type        = string
  default     = "INR"
}

variable "existing_budget_id" {
  description = "ID of the hard-cap budget created with gcloud, adopted by imports.tf."
  type        = string
}

variable "sql_tier" {
  description = "db-f1-micro is shared-core: cheapest (~$8-12/month) but outside the Cloud SQL SLA. Move to db-custom-1-3840 or larger when ShipCube has paying users."
  type        = string
  default     = "db-f1-micro"
}

variable "sql_availability" {
  description = "ZONAL, or REGIONAL for a standby in a second zone (roughly doubles instance cost)."
  type        = string
  default     = "ZONAL"
}

variable "migration_operators" {
  description = "Principals that may impersonate sc-migrator to run schema migrations (people and CI)."
  type        = list(string)
  default     = []
}

variable "ml_bucket_name" {
  description = "Model registry and profiler source uploads, in the control project."
  type        = string
  default     = "shipcube-ks-ml"
}

variable "uploads_bucket_name" {
  description = "Source uploads for deploys (created by networking's gcp_setup.sh, adopted here)."
  type        = string
  default     = "shipcube-uploads-ks"
}

variable "postgres_password_version" {
  description = "Bump to rotate the postgres password (Secret Manager gets a new version too)."
  type        = number
  default     = 1
}


variable "alert_email" {
  description = "Where uptime and alert notifications go. Set it in terraform.tfvars (gitignored), never in the repo."
  type        = string
  sensitive   = true
}
