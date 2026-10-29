variable "control_project_id" {
  type = string
}

variable "apps_project_id" {
  description = "Project where hosted apps build and run."
  type        = string
}

variable "apps_project_number" {
  type = string
}

variable "uploads_bucket" {
  description = "Source uploads bucket; per-workspace managed folders live under ws/."
  type        = string
}

variable "worker_member" {
  description = "The only principal allowed to impersonate the provisioner (sc-worker)."
  type        = string
}
