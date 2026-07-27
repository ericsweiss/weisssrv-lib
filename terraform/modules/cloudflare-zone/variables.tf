variable "account_id" {
  description = "Cloudflare account ID that owns the zone (32 hex chars)."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{32}$", var.account_id))
    error_message = "account_id must be a 32-character hex string (the Cloudflare account ID)."
  }
}

variable "zone_name" {
  description = "Zone name (bare FQDN, e.g. example.com)."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9-]+(\\.[a-z0-9-]+)*\\.[a-z]{2,}$", var.zone_name))
    error_message = "zone_name must be a bare FQDN with no scheme, trailing dot, or slash."
  }
}

variable "manage_zone_settings" {
  description = <<-EOT
    Manage zone-wide TLS/cache settings. Requires Zone Settings:Edit on the
    provider token. Flipping true -> false DESTROYS the override resource, which
    reverts the zone to the settings captured when it was created.
  EOT
  type        = bool
  default     = true
}

variable "zone_settings" {
  description = "Zone-wide settings applied when manage_zone_settings is true. Defaults are the hardened baseline (strict SSL, forced HTTPS, HSTS)."
  type = object({
    ssl                      = optional(string, "strict")
    always_use_https         = optional(string, "on")
    min_tls_version          = optional(string, "1.2")
    automatic_https_rewrites = optional(string, "on")
    tls_1_3                  = optional(string, "on")
    http3                    = optional(string, "on")
    zero_rtt                 = optional(string, "off")
    early_hints              = optional(string, "off")
    brotli                   = optional(string, "on")
    cache_level              = optional(string, "aggressive")
    browser_cache_ttl        = optional(number, 14400)
    development_mode         = optional(string, "off")
    hsts = optional(object({
      enabled            = optional(bool, true)
      max_age            = optional(number, 31536000)
      include_subdomains = optional(bool, true)
      nosniff            = optional(bool, true)
      # preload defaults off: submission to the browser HSTS preload list is a
      # hard-to-reverse commitment.
      preload = optional(bool, false)
    }), {})
  })
  default = {}
}

variable "records" {
  description = <<-EOT
    DNS records keyed by a stable identity string. The key is the state address:
    renaming it destroys and recreates the record unless the caller adds a
    `moved {}` block.

    Per-record flags select the lifecycle class (see README "Protecting
    catastrophic records"):
      protected                  - resource carries lifecycle.prevent_destroy
      content_managed_externally - resource ignores drift on `content`
                                   (DDNS/external-dns owns the value)
    Changing either flag moves the record to a different resource address.
  EOT
  type = map(object({
    name     = string
    type     = string
    content  = optional(string)
    priority = optional(number)
    proxied  = optional(bool, false)
    ttl      = optional(number, 1)
    comment  = optional(string)
    # Structured RDATA. Only the CAA triple is modelled; other structured types
    # (SRV, DS, ...) need a module change.
    record_data = optional(object({
      flags = optional(number)
      tag   = optional(string)
      value = optional(string)
    }))
    protected                  = optional(bool, false)
    content_managed_externally = optional(bool, false)
  }))
  default = {}

  validation {
    condition = alltrue([
      for key, r in var.records :
      contains(["A", "AAAA", "CNAME", "TXT", "MX", "NS", "CAA"], upper(r.type))
    ])
    error_message = "records[*].type must be one of A, AAAA, CNAME, TXT, MX, NS, CAA."
  }

  validation {
    condition = alltrue([
      for key, r in var.records : (r.content == null) != (r.record_data == null)
    ])
    error_message = "Each record must set exactly one of content or record_data (CAA uses record_data)."
  }

  validation {
    condition = alltrue([
      for key, r in var.records : r.ttl == 1 if r.proxied
    ])
    error_message = "Proxied records must use ttl = 1 (Cloudflare 'Auto'); any other TTL is rejected by the API."
  }

  validation {
    condition = alltrue([
      for key, r in var.records : r.priority != null if upper(r.type) == "MX"
    ])
    error_message = "MX records must set priority."
  }
}
