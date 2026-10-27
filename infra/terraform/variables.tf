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
  description = "Budget for the credit pool, in budget_currency (the name is historical)."
  type        = number
  default     = 24000
}

variable "budget_currency" {
  description = "Must match the billing account's currency."
  type        = string
  default     = "INR"
}
