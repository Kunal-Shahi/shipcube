variable "project_id" {
  description = "Project where profiling jobs and their builds run (the apps project)."
  type        = string
}

variable "region" {
  type = string
}

variable "ml_bucket_project" {
  description = "Project holding the ML bucket (the control project)."
  type        = string
}

variable "ml_bucket_name" {
  type = string
}

variable "estimator_members" {
  description = "Identities that run estimate() and create profiling jobs (the API and the worker)."
  type        = list(string)
}

variable "subnet_cidr" {
  type    = string
  default = "10.80.0.0/24"
}

variable "offline_builds" {
  description = "Phase 2 (#22): AR mirrors of PyPI and Docker Hub plus a private Cloud Build pool without internet. Billable; needs Kunal's OK."
  type        = bool
  default     = false
}
