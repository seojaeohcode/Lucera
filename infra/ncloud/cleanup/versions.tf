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
  region      = "KR"
  site        = "public"
  support_vpc = true
}
