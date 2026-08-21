output "network_ids" {
  description = "Network id per `networks` key."
  value       = local.network_ids
}

output "zone_ids" {
  description = "Firewall-zone id per zone key — the custom `zones` keys plus the `builtin_zone_names` keys a policy endpoint references, in one map. That is the same namespace `policies[*].source.zone` resolves against; an unreferenced built-in is never read, so it is absent here too."
  value       = local.zone_ids
}

output "wlan_ids" {
  description = "WLAN id per `wlans` key."
  value       = { for key, w in unifi_wlan.this : key => w.id }
}
