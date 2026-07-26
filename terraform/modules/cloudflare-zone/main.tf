# account_id scopes the lookup — zone names are not unique across accounts, so a
# name-only lookup can resolve a like-named zone the token happens to see.
data "cloudflare_zone" "this" {
  account_id = var.account_id
  name       = var.zone_name
}

resource "cloudflare_zone_settings_override" "this" {
  count = var.manage_zone_settings ? 1 : 0

  zone_id = data.cloudflare_zone.this.id

  settings {
    # http2, polish, mirage and webp are read-only via the API; Auto Minify was
    # retired by Cloudflare in 2024 (no minify setting exists).
    ssl                      = var.zone_settings.ssl
    always_use_https         = var.zone_settings.always_use_https
    min_tls_version          = var.zone_settings.min_tls_version
    automatic_https_rewrites = var.zone_settings.automatic_https_rewrites
    tls_1_3                  = var.zone_settings.tls_1_3
    http3                    = var.zone_settings.http3
    zero_rtt                 = var.zone_settings.zero_rtt
    early_hints              = var.zone_settings.early_hints
    brotli                   = var.zone_settings.brotli
    cache_level              = var.zone_settings.cache_level
    browser_cache_ttl        = var.zone_settings.browser_cache_ttl
    development_mode         = var.zone_settings.development_mode

    security_header { # HSTS
      enabled            = var.zone_settings.hsts.enabled
      max_age            = var.zone_settings.hsts.max_age
      include_subdomains = var.zone_settings.hsts.include_subdomains
      nosniff            = var.zone_settings.hsts.nosniff
      preload            = var.zone_settings.hsts.preload
    }
  }
}

# `lifecycle` takes no variables, so the two protection dimensions are expressed
# as four resources and records are routed by their flags.
locals {
  records_plain = {
    for key, r in var.records : key => r
    if !r.protected && !r.content_managed_externally
  }
  records_protected = {
    for key, r in var.records : key => r
    if r.protected && !r.content_managed_externally
  }
  records_external_content = {
    for key, r in var.records : key => r
    if !r.protected && r.content_managed_externally
  }
  records_protected_external_content = {
    for key, r in var.records : key => r
    if r.protected && r.content_managed_externally
  }
}

resource "cloudflare_record" "this" {
  for_each = local.records_plain

  zone_id  = data.cloudflare_zone.this.id
  name     = each.value.name
  type     = each.value.type
  content  = each.value.content
  priority = each.value.priority
  proxied  = each.value.proxied
  ttl      = each.value.ttl
  comment  = each.value.comment

  dynamic "data" {
    for_each = each.value.record_data == null ? [] : [each.value.record_data]
    iterator = rdata
    content {
      flags = rdata.value.flags
      tag   = rdata.value.tag
      value = rdata.value.value
    }
  }
}

resource "cloudflare_record" "protected" {
  for_each = local.records_protected

  zone_id  = data.cloudflare_zone.this.id
  name     = each.value.name
  type     = each.value.type
  content  = each.value.content
  priority = each.value.priority
  proxied  = each.value.proxied
  ttl      = each.value.ttl
  comment  = each.value.comment

  dynamic "data" {
    for_each = each.value.record_data == null ? [] : [each.value.record_data]
    iterator = rdata
    content {
      flags = rdata.value.flags
      tag   = rdata.value.tag
      value = rdata.value.value
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "cloudflare_record" "external_content" {
  for_each = local.records_external_content

  zone_id  = data.cloudflare_zone.this.id
  name     = each.value.name
  type     = each.value.type
  content  = each.value.content
  priority = each.value.priority
  proxied  = each.value.proxied
  ttl      = each.value.ttl
  comment  = each.value.comment

  dynamic "data" {
    for_each = each.value.record_data == null ? [] : [each.value.record_data]
    iterator = rdata
    content {
      flags = rdata.value.flags
      tag   = rdata.value.tag
      value = rdata.value.value
    }
  }

  lifecycle {
    # `content` is seeded once and then owned by the external updater; proxied
    # and ttl stay Terraform-owned.
    ignore_changes = [content]
  }
}

resource "cloudflare_record" "protected_external_content" {
  for_each = local.records_protected_external_content

  zone_id  = data.cloudflare_zone.this.id
  name     = each.value.name
  type     = each.value.type
  content  = each.value.content
  priority = each.value.priority
  proxied  = each.value.proxied
  ttl      = each.value.ttl
  comment  = each.value.comment

  dynamic "data" {
    for_each = each.value.record_data == null ? [] : [each.value.record_data]
    iterator = rdata
    content {
      flags = rdata.value.flags
      tag   = rdata.value.tag
      value = rdata.value.value
    }
  }

  lifecycle {
    ignore_changes  = [content]
    prevent_destroy = true
  }
}
