locals {
  resource_prefix = "${var.name_prefix}-${var.environment}"
  key_name        = "${local.resource_prefix}-login"
}

resource "ncloud_vpc" "main" {
  name            = "${local.resource_prefix}-vpc"
  ipv4_cidr_block = var.vpc_cidr
}

resource "ncloud_subnet" "public" {
  name           = "${local.resource_prefix}-public"
  vpc_no         = ncloud_vpc.main.id
  subnet         = var.subnet_cidr
  zone           = var.zone
  subnet_type    = "PUBLIC"
  network_acl_no = ncloud_vpc.main.default_network_acl_no
  usage_type     = "GEN"
}

resource "ncloud_login_key" "main" {
  key_name = local.key_name
}

data "ncloud_server_image_numbers" "kvm" {
  server_image_name = var.server_image_name

  filter {
    name   = "hypervisor_type"
    values = ["KVM"]
  }
}

resource "ncloud_server" "main" {
  name                          = "${local.resource_prefix}-api"
  server_image_number           = data.ncloud_server_image_numbers.kvm.image_number_list[0].server_image_number
  server_spec_code              = var.server_spec_code
  login_key_name                = ncloud_login_key.main.key_name
  subnet_no                     = ncloud_subnet.public.id
  is_protect_server_termination = false
}

resource "ncloud_public_ip" "main" {
  server_instance_no = ncloud_server.main.instance_no
}

resource "ncloud_access_control_group" "web" {
  name        = "${local.resource_prefix}-web"
  description = "Lucera API web ingress"
  vpc_no      = ncloud_vpc.main.id
}

resource "ncloud_access_control_group_rule" "web" {
  access_control_group_no = ncloud_access_control_group.web.id

  inbound {
    protocol    = "TCP"
    ip_block    = "0.0.0.0/0"
    port_range  = "80"
    description = "HTTP"
  }

  inbound {
    protocol    = "TCP"
    ip_block    = "0.0.0.0/0"
    port_range  = "443"
    description = "HTTPS"
  }

  outbound {
    protocol    = "TCP"
    ip_block    = "0.0.0.0/0"
    port_range  = "1-65535"
    description = "TCP egress"
  }
}

resource "ncloud_network_interface" "main" {
  subnet_no          = ncloud_subnet.public.id
  server_instance_no = ncloud_server.main.instance_no
  access_control_groups = [
    ncloud_vpc.main.default_access_control_group_no,
    ncloud_access_control_group.web.id,
  ]
}

data "ncloud_root_password" "main" {
  server_instance_no = ncloud_server.main.instance_no
  private_key        = ncloud_login_key.main.private_key
}
