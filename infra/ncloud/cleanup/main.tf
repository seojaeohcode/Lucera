# This root is intentionally populated only with resources discovered by the
# Terraform inventory. Import the exact IDs before running destroy.

resource "ncloud_vpc" "existing" {
  name            = "life-rpg-demo-vpc"
  ipv4_cidr_block = "10.20.0.0/16"
}

resource "ncloud_subnet" "existing_public" {
  name           = "life-rpg-demo-public"
  vpc_no         = "145704"
  subnet         = "10.20.10.0/24"
  zone           = "KR-2"
  subnet_type    = "PUBLIC"
  network_acl_no = "196216"
  usage_type     = "GEN"
}

resource "ncloud_access_control_group" "existing_custom" {
  vpc_no = "145704"
}

resource "ncloud_login_key" "existing" {
  key_name = "life-rpg-demo-login"
}

resource "ncloud_public_ip" "existing_unassociated" {}
