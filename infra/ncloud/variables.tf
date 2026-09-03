variable "region" {
  description = "NCloud region code."
  type        = string
  default     = "KR"
}

variable "site" {
  description = "NCloud site."
  type        = string
  default     = "public"
}

variable "environment" {
  description = "Environment suffix used to keep Terraform-managed names isolated."
  type        = string
  default     = "prod"
}

variable "name_prefix" {
  description = "Prefix for all resources created by this stack."
  type        = string
  default     = "lucera"
}

variable "vpc_cidr" {
  description = "Private IPv4 range for the application VPC."
  type        = string
  default     = "10.0.0.0/16"
}

variable "subnet_cidr" {
  description = "Public subnet IPv4 range."
  type        = string
  default     = "10.0.1.0/24"
}

variable "zone" {
  description = "NCloud availability zone. Confirm availability in the account during plan."
  type        = string
  default     = "KR-2"
}

variable "server_image_name" {
  description = "KVM image name queried through the NCloud data source."
  type        = string
  default     = "ubuntu-22.04"
}

variable "server_spec_code" {
  description = "Server product code. Keep this configurable for account/zone availability."
  type        = string
  default     = "s2-g3"
}
