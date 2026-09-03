terraform {
  required_version = ">= 1.5.0"

  required_providers {
    ncloud = {
      source  = "NaverCloudPlatform/ncloud"
      version = "4.0.7"
    }
  }
}

provider "ncloud" {
  region      = var.region
  site        = var.site
  support_vpc = true
}

variable "region" {
  type    = string
  default = "KR"
}

variable "site" {
  type    = string
  default = "public"
}
