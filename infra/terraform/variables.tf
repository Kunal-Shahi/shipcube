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

variable "budget_amount_usd" {
  description = "Budget for the credit pool. Alerts at 17%, 50% and 83% (about $50/$150/$250 of $300)."
  type        = number
  default     = 300
}
