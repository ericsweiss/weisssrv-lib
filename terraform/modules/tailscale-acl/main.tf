resource "tailscale_acl" "this" {
  acl = var.acl_policy

  # Do NOT revert the tailnet to the default allow-all ACL on destroy: once the
  # policy is tighter than allow-all that is a silent security regression, not a
  # rollback.
  reset_acl_on_destroy = false

  # Not an input — `lifecycle` blocks take no variables, so this is fixed for
  # every consumer. Stop managing the ACL with
  # `terraform state rm 'module.<name>.tailscale_acl.this'` (the live policy is
  # untouched, since reset_acl_on_destroy is false), then drop the module block.
  # See README.md "Apply is supervised".
  lifecycle {
    prevent_destroy = true
  }
}

data "tailscale_device" "split_dns" {
  for_each = {
    for domain, cfg in var.split_dns : domain => cfg
    if cfg.device_hostname != null
  }

  hostname = each.value.device_hostname
  # Literal, not an input: the provider parses this duration during
  # `terraform validate`, where any variable reference is still unknown and the
  # parse fails.
  wait_for = "60s"
}

resource "tailscale_dns_split_nameservers" "this" {
  for_each = var.split_dns

  domain = each.key
  # addresses[0] is the device's IPv4 (100.x) tailnet address.
  nameservers = (
    each.value.device_hostname == null
    ? each.value.nameservers
    : [data.tailscale_device.split_dns[each.key].addresses[0]]
  )
}
