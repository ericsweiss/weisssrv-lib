# `terraform validate` evaluates no caller values, so nothing else exercises the
# variable validations, the record routing or the lifecycle split. Runs are
# plan-only except the seeded pair at the end, which needs state.
mock_provider "cloudflare" {}

variables {
  account_id = "0123456789abcdef0123456789abcdef"
  zone_name  = "example.com"
}

run "hardened_defaults_plan" {
  command = plan

  assert {
    condition     = length(cloudflare_zone_settings_override.this) == 1
    error_message = "manage_zone_settings defaults to true, so the override must be planned."
  }
}

run "manage_zone_settings_false_drops_the_override" {
  command = plan

  variables {
    manage_zone_settings = false
  }

  assert {
    condition     = length(cloudflare_zone_settings_override.this) == 0
    error_message = "manage_zone_settings = false must plan no override resource."
  }
}

run "rejects_a_non_hex_account_id" {
  command = plan

  variables {
    account_id = "my-cloudflare-account"
  }

  expect_failures = [var.account_id]
}

run "rejects_a_zone_name_with_a_scheme" {
  command = plan

  variables {
    zone_name = "https://example.com"
  }

  expect_failures = [var.zone_name]
}

run "rejects_an_unknown_ssl_mode" {
  command = plan

  variables {
    zone_settings = { ssl = "Strict" }
  }

  expect_failures = [var.zone_settings]
}

run "rejects_an_unknown_min_tls_version" {
  command = plan

  variables {
    zone_settings = { min_tls_version = "1.2.0" }
  }

  expect_failures = [var.zone_settings]
}

run "rejects_an_unknown_cache_level" {
  command = plan

  variables {
    zone_settings = { cache_level = "everything" }
  }

  expect_failures = [var.zone_settings]
}

run "rejects_a_non_toggle_value" {
  command = plan

  variables {
    zone_settings = { brotli = "true" }
  }

  expect_failures = [var.zone_settings]
}

run "rejects_an_unknown_tls_1_3_value" {
  command = plan

  variables {
    zone_settings = { tls_1_3 = "yes" }
  }

  expect_failures = [var.zone_settings]
}

run "rejects_a_browser_cache_ttl_above_the_maximum" {
  command = plan

  variables {
    zone_settings = { browser_cache_ttl = 31536001 }
  }

  expect_failures = [var.zone_settings]
}

# max_age = 0 is how HSTS is withdrawn, so it must not be reachable while the
# header is still enabled.
run "rejects_hsts_enabled_with_a_zero_max_age" {
  command = plan

  variables {
    zone_settings = { hsts = { max_age = 0 } }
  }

  expect_failures = [var.zone_settings]
}

run "rejects_preload_below_the_twelve_month_floor" {
  command = plan

  variables {
    zone_settings = { hsts = { max_age = 2592000, preload = true } }
  }

  expect_failures = [var.zone_settings]
}

run "rejects_preload_without_include_subdomains" {
  command = plan

  variables {
    zone_settings = { hsts = { preload = true, include_subdomains = false } }
  }

  expect_failures = [var.zone_settings]
}

run "accepts_a_preload_ready_hsts_policy" {
  command = plan

  variables {
    zone_settings = { hsts = { preload = true } }
  }

  assert {
    condition     = length(cloudflare_zone_settings_override.this) == 1
    error_message = "The default 12-month, include-subdomains HSTS policy satisfies the preload floor and must plan."
  }
}

run "accepts_zrt_for_tls_1_3" {
  command = plan

  variables {
    zone_settings = { tls_1_3 = "zrt" }
  }

  assert {
    condition     = length(cloudflare_zone_settings_override.this) == 1
    error_message = "tls_1_3 = \"zrt\" is a valid Cloudflare value and must plan."
  }
}

run "rejects_an_unsupported_record_type" {
  command = plan

  variables {
    records = {
      srv = { name = "_sip._tcp", type = "SRV", content = "0 0 5060 sip.example.com" }
    }
  }

  expect_failures = [var.records]
}

run "rejects_a_record_with_both_content_and_record_data" {
  command = plan

  variables {
    records = {
      caa = {
        name        = "@"
        type        = "CAA"
        content     = "0 issue letsencrypt.org"
        record_data = { flags = 0, tag = "issue", value = "letsencrypt.org" }
      }
    }
  }

  expect_failures = [var.records]
}

run "rejects_a_record_with_neither_content_nor_record_data" {
  command = plan

  variables {
    records = {
      bare = { name = "www", type = "A" }
    }
  }

  expect_failures = [var.records]
}

run "rejects_a_proxied_record_with_an_explicit_ttl" {
  command = plan

  variables {
    records = {
      www = { name = "www", type = "A", content = "203.0.113.10", proxied = true, ttl = 300 }
    }
  }

  expect_failures = [var.records]
}

run "rejects_an_mx_record_without_priority" {
  command = plan

  variables {
    records = {
      mail = { name = "@", type = "MX", content = "mx.example.com" }
    }
  }

  expect_failures = [var.records]
}

# Wrong routing silently drops a record's prevent_destroy or its ignore_changes.
run "flags_route_each_record_to_its_lifecycle_class" {
  command = plan

  variables {
    records = {
      plain = { name = "plain", type = "A", content = "203.0.113.10" }
      keep = {
        name      = "keep"
        type      = "A"
        content   = "203.0.113.11"
        protected = true
      }
      ddns = {
        name                       = "ddns"
        type                       = "A"
        content                    = "203.0.113.12"
        content_managed_externally = true
      }
      root = {
        name                       = "@"
        type                       = "A"
        content                    = "203.0.113.13"
        protected                  = true
        content_managed_externally = true
      }
      caa = {
        name        = "@"
        type        = "CAA"
        record_data = { flags = 0, tag = "issue", value = "letsencrypt.org" }
        protected   = true
      }
    }
  }

  assert {
    condition     = keys(cloudflare_record.this) == ["plain"]
    error_message = "Unflagged records belong on cloudflare_record.this."
  }

  assert {
    condition     = keys(cloudflare_record.protected) == ["caa", "keep"]
    error_message = "protected = true (alone) belongs on cloudflare_record.protected."
  }

  assert {
    condition     = keys(cloudflare_record.external_content) == ["ddns"]
    error_message = "content_managed_externally = true (alone) belongs on cloudflare_record.external_content."
  }

  assert {
    condition     = keys(cloudflare_record.protected_external_content) == ["root"]
    error_message = "Both flags belong on cloudflare_record.protected_external_content."
  }

  assert {
    condition     = one(cloudflare_record.protected["caa"].data).value == "letsencrypt.org"
    error_message = "record_data must populate the record's dynamic data block."
  }
}

# The four classes differ only in their lifecycle block, and `lifecycle` takes no
# variables, so the argument sets are copies. This run fails when one copy gains
# or loses an argument the others keep.
run "every_lifecycle_class_renders_the_same_argument_set" {
  command = plan

  variables {
    records = {
      a = { name = "parity", type = "MX", content = "mx.example.com", priority = 10, ttl = 300, comment = "parity" }
      b = { name = "parity", type = "MX", content = "mx.example.com", priority = 10, ttl = 300, comment = "parity", protected = true }
      c = { name = "parity", type = "MX", content = "mx.example.com", priority = 10, ttl = 300, comment = "parity", content_managed_externally = true }
      d = { name = "parity", type = "MX", content = "mx.example.com", priority = 10, ttl = 300, comment = "parity", protected = true, content_managed_externally = true }
    }
  }

  assert {
    condition = alltrue([
      for r in [
        cloudflare_record.protected["b"],
        cloudflare_record.external_content["c"],
        cloudflare_record.protected_external_content["d"],
      ] :
      r.name == cloudflare_record.this["a"].name &&
      r.type == cloudflare_record.this["a"].type &&
      r.content == cloudflare_record.this["a"].content &&
      r.priority == cloudflare_record.this["a"].priority &&
      r.proxied == cloudflare_record.this["a"].proxied &&
      r.ttl == cloudflare_record.this["a"].ttl &&
      r.comment == cloudflare_record.this["a"].comment
    ])
    error_message = "The four lifecycle classes must render an identical argument set."
  }
}

# A lower-case type clears the case-insensitive validation, so the module has to
# normalise it rather than hand the provider a value its own enum rejects.
run "a_lower_case_record_type_is_normalised" {
  command = plan

  variables {
    records = {
      plain = { name = "plain", type = "a", content = "203.0.113.10" }
    }
  }

  assert {
    condition     = cloudflare_record.this["plain"].type == "A"
    error_message = "records[*].type must reach the provider upper-cased."
  }
}

# ignore_changes is invisible to a create plan, so this pair seeds state first.
# `content` on these two classes belongs to the external updater.
run "seed_externally_managed_content" {
  command = apply

  variables {
    records = {
      ddns = {
        name                       = "ddns"
        type                       = "A"
        content                    = "192.0.2.1"
        content_managed_externally = true
      }
      root = {
        name                       = "@"
        type                       = "A"
        content                    = "192.0.2.1"
        protected                  = true
        content_managed_externally = true
      }
    }
  }
}

run "externally_managed_content_ignores_a_content_change" {
  command = plan

  variables {
    records = {
      ddns = {
        name                       = "ddns"
        type                       = "A"
        content                    = "203.0.113.9"
        content_managed_externally = true
      }
      root = {
        name                       = "@"
        type                       = "A"
        content                    = "203.0.113.9"
        protected                  = true
        content_managed_externally = true
      }
    }
  }

  assert {
    condition     = cloudflare_record.external_content["ddns"].content == "192.0.2.1"
    error_message = "ignore_changes = [content] must keep the seeded value; the external updater owns it."
  }

  assert {
    condition     = cloudflare_record.protected_external_content["root"].content == "192.0.2.1"
    error_message = "ignore_changes = [content] must keep the seeded value on the protected class too."
  }
}
