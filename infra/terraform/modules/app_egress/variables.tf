variable "project_id" {
  description = "Project where hosted apps run (the apps project)."
  type        = string
}

variable "region" {
  type = string
}

variable "network_name" {
  description = "Name of the mediated-egress VPC. Deployed hosted apps attach to it with Direct VPC egress (SHIPCUBE_APP_EGRESS_NETWORK)."
  type        = string
  default     = "app-egress"
}

variable "subnet_name" {
  description = "Subnet hosted apps send all egress through (SHIPCUBE_APP_EGRESS_SUBNET)."
  type        = string
  default     = "app-egress"
}

variable "subnet_cidr" {
  description = "Direct VPC egress uses one address per running instance, so size for peak instances across all hosted apps."
  type        = string
  default     = "10.90.0.0/20"
}

variable "allowed_tcp_ports" {
  description = "Outbound TCP ports hosted apps may reach on the internet. 443/80 lets ordinary HTTPS/HTTP API calls through; everything else (stratum, SMTP, arbitrary C2 ports) is denied."
  type        = list(string)
  default     = ["80", "443"]
}
