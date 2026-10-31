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

# --- Runtime abuse alerts on hosted apps (abuse.tf) -------------------------------------------

variable "apps_project_id" {
  description = "Project where ShipCube hosts users' apps. Added to this project's metrics scope so the abuse alerts can see its Cloud Run metrics."
  type        = string
}

variable "abuse_cpu_utilization" {
  description = "Median CPU utilization of a hosted app's instances that counts as pegged (mining signature)."
  type        = number
  default     = 0.9
}

variable "abuse_cpu_minutes" {
  description = "How long the CPU must stay pegged before alerting."
  type        = number
  default     = 30
}

variable "abuse_egress_bytes_per_s" {
  description = "Outbound bytes/s per hosted app, sustained 10 min. 1 MB/s is ~600 MB in 10 minutes: far above an internal tool's JSON/HTML responses, well below a bulk exfiltration or spam/DDoS source."
  type        = number
  default     = 1000000
}

variable "abuse_requests_per_s" {
  description = "Requests/s per hosted app, sustained 10 min. 20/s is 10x the default workspace cap of 120 requests/minute."
  type        = number
  default     = 20
}
