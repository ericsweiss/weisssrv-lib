variable "acl_policy" {
  description = <<-EOT
    Tailnet policy document (HuJSON) as a string. Pass the site's policy file
    from the root module, e.g. `file("$${path.module}/policy.hujson")` — the
    module cannot read the caller's file itself because `path.module` inside a
    module resolves to the module directory.
  EOT
  type        = string

  validation {
    condition     = length(trimspace(var.acl_policy)) > 0
    error_message = "acl_policy must not be empty — an empty policy would replace the tailnet ACL with nothing."
  }
}

variable "split_dns" {
  description = <<-EOT
    Split-DNS nameservers per domain. REQUIRED and intentionally without a
    default: an unset value must be a hard error, never a silently-planned
    destroy of live Split-DNS (pass `{}` for a tailnet that manages none).

    Each entry sets exactly one source:
      nameservers     - literal resolver IPs
      device_hostname - a tailnet device whose IPv4 (100.x) address is resolved
                        at plan time, so a rebuilt device self-heals
  EOT
  type = map(object({
    nameservers     = optional(list(string), [])
    device_hostname = optional(string)
  }))

  validation {
    condition = alltrue([
      for domain, cfg in var.split_dns :
      (cfg.device_hostname == null) != (length(cfg.nameservers) == 0)
    ])
    error_message = "Each split_dns entry must set exactly one of nameservers or device_hostname."
  }

  validation {
    condition = alltrue([
      for domain, cfg in var.split_dns :
      can(regex("^[a-z0-9-]+(\\.[a-z0-9-]+)*\\.[a-z]{2,}$", domain))
    ])
    error_message = "split_dns keys must be bare FQDNs with no scheme, trailing dot, or slash."
  }
}
