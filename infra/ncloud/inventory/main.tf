data "ncloud_vpcs" "all" {}

data "ncloud_subnets" "all" {}

data "ncloud_servers" "all" {}

output "vpcs" {
  value = data.ncloud_vpcs.all.vpcs
}

output "subnets" {
  value = data.ncloud_subnets.all.subnets
}

output "server_ids" {
  value = data.ncloud_servers.all.ids
}
