# `terraform validate` evaluates no caller values, so nothing else exercises the
# variable validations, the record routing or the lifecycle split. Every run is
# `command = plan`: a plan creates no state, so the file needs no teardown —
# which the module's `prevent_destroy` resources would refuse anyway.
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

# The two flags select one of four resource addresses, and getting that routing
# wrong silently drops a record's prevent_destroy or its ignore_changes.
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
