output "acl_id" {
  description = "ID of the managed tailnet ACL resource."
  value       = tailscale_acl.this.id
}

output "split_dns_nameservers" {
  description = "Resolved nameserver IPs per Split-DNS domain (device hostnames already resolved)."
  value       = { for domain, r in tailscale_dns_split_nameservers.this : domain => r.nameservers }
}
