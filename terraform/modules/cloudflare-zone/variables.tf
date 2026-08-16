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
    provider token.

    Flipping true -> false alone is a hard plan error: the override carries
    `prevent_destroy`, which Terraform enforces against any plan that destroys
    the instance, count-driven removals included. Stop managing the settings in
    two steps —
    `terraform state rm 'module.<name>.cloudflare_zone_settings_override.this[0]'`
    (the live zone settings survive that), then set the variable false.
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

  # The API rejects an out-of-set value mid-apply, after the earlier settings in
  # the same override have already been written.
  validation {
    condition     = contains(["off", "flexible", "full", "strict", "origin_pull"], var.zone_settings.ssl)
    error_message = "zone_settings.ssl must be one of off, flexible, full, strict, origin_pull (lower case)."
  }

  validation {
    condition     = contains(["1.0", "1.1", "1.2", "1.3"], var.zone_settings.min_tls_version)
    error_message = "zone_settings.min_tls_version must be one of 1.0, 1.1, 1.2, 1.3."
  }

  validation {
    condition     = contains(["aggressive", "basic", "simplified"], var.zone_settings.cache_level)
    error_message = "zone_settings.cache_level must be one of aggressive, basic, simplified."
  }

  # tls_1_3 is not a plain toggle: "zrt" enables TLS 1.3 with 0-RTT.
  validation {
    condition     = contains(["on", "off", "zrt"], var.zone_settings.tls_1_3)
    error_message = "zone_settings.tls_1_3 must be \"on\", \"off\", or \"zrt\"."
  }

  validation {
    condition = alltrue([
      for toggle in [
        var.zone_settings.always_use_https,
        var.zone_settings.automatic_https_rewrites,
        var.zone_settings.http3,
        var.zone_settings.zero_rtt,
        var.zone_settings.early_hints,
        var.zone_settings.brotli,
        var.zone_settings.development_mode,
      ] : contains(["on", "off"], toggle)
    ])
    error_message = "zone_settings on/off toggles (always_use_https, automatic_https_rewrites, http3, zero_rtt, early_hints, brotli, development_mode) must be \"on\" or \"off\"."
  }

  # The two numeric settings are bounded rather than enumerated: the accepted
  # step values vary by plan, but the bounds and the preload floor do not.
  validation {
    condition = (
      var.zone_settings.browser_cache_ttl >= 0
      && var.zone_settings.browser_cache_ttl <= 31536000
    )
    error_message = "zone_settings.browser_cache_ttl must be 0-31536000 seconds (0 = respect the origin's own Cache-Control)."
  }

  validation {
    condition = (
      !var.zone_settings.hsts.enabled
      || var.zone_settings.hsts.max_age > 0
    )
    error_message = "zone_settings.hsts.max_age must be > 0 while hsts.enabled is true. max_age = 0 is how HSTS is WITHDRAWN — it tells every browser to forget the policy — so turn the header off with enabled = false instead of leaving it on with no lifetime."
  }

  validation {
    condition = (
      !var.zone_settings.hsts.preload
      || (
        var.zone_settings.hsts.enabled
        && var.zone_settings.hsts.include_subdomains
        && var.zone_settings.hsts.max_age >= 31536000
      )
    )
    error_message = "zone_settings.hsts.preload requires enabled = true, include_subdomains = true and max_age >= 31536000 (12 months). The browser preload lists refuse a submission missing any of the three, so a shorter policy advertises a preload that will never be honoured."
  }
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
