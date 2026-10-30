variable "project_id" {
  description = "Project running ShipCube's own Cloud Run services and Cloud SQL (the control project)."
  type        = string
}

variable "project_number" {
  description = "Its number, which appears in the services' run.app hostnames."
  type        = string
}

variable "region" {
  type = string
}

variable "alert_email" {
  description = "Where alerts go (one email notification channel)."
  type        = string
  sensitive   = true
}

variable "uptime_paths" {
  description = "Cloud Run service -> path its uptime check calls. /health, not /healthz: Cloud Run reserves /healthz on run.app URLs."
  type        = map(string)
  default = {
    "shipcube-console" = "/api/healthz" # 500 naming the bad setting when the console is misconfigured
    "shipcube-mcp"     = "/health"
    "shipcube-gateway" = "/health"
    "shipcube-worker"  = "/health" # 503 when the queue loop died or stalled
  }
}

variable "private_services" {
  description = "Services that aren't public: their uptime check sends an OIDC token from the Monitoring service agent, which deploy/deploy.sh grants run.invoker."
  type        = set(string)
  default     = ["shipcube-worker"]
}

variable "worker_service" {
  type    = string
  default = "shipcube-worker"
}

variable "sql_instance" {
  description = "Cloud SQL instance name."
  type        = string
}
