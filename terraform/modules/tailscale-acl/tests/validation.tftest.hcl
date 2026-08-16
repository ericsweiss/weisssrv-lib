# `terraform validate` evaluates no caller values, so nothing else exercises the
# split_dns validations, the IPv4 selection or its precondition. Every run is
# `command = plan`: a plan creates no state, so the file needs no teardown —
# which the module's `prevent_destroy` resources would refuse anyway.
mock_provider "tailscale" {
  # One IPv4 and one IPv6, the shape a real tailnet device returns.
  mock_data "tailscale_device" {
    defaults = {
      addresses = ["100.64.0.10", "fd7a:115c:a1e0::1"]
    }
  }
}

variables {
  acl_policy = <<-EOT
    {
      "acls": [{"action": "accept", "src": ["*"], "dst": ["*:*"]}],
    }
  EOT
  split_dns  = {}
}

run "empty_split_dns_plans_the_acl_alone" {
  command = plan

  assert {
    condition     = length(tailscale_dns_split_nameservers.this) == 0
    error_message = "split_dns = {} must plan no split-DNS entries."
  }

  assert {
    condition     = tailscale_acl.this.reset_acl_on_destroy == false
    error_message = "A destroy must never revert the tailnet to the default allow-all policy."
  }
}

run "rejects_an_empty_acl_policy" {
  command = plan

  variables {
    acl_policy = "   \n"
  }

  expect_failures = [var.acl_policy]
}

run "rejects_an_entry_with_neither_source" {
  command = plan

  variables {
    split_dns = {
      "internal.example.com" = {}
    }
  }

  expect_failures = [var.split_dns]
}

run "rejects_an_entry_with_both_sources" {
  command = plan

  variables {
    split_dns = {
      "internal.example.com" = {
        nameservers     = ["100.64.0.10"]
        device_hostname = "ts-dns"
      }
    }
  }

  expect_failures = [var.split_dns]
}

run "rejects_a_domain_that_is_not_a_bare_fqdn" {
  command = plan

  variables {
    split_dns = {
      "https://internal.example.com" = { nameservers = ["100.64.0.10"] }
    }
  }

  expect_failures = [var.split_dns]
}

run "literal_nameservers_pass_through" {
  command = plan

  variables {
    split_dns = {
      "lab.example.com" = { nameservers = ["100.64.0.10", "100.64.0.11"] }
    }
  }

  assert {
    condition     = tailscale_dns_split_nameservers.this["lab.example.com"].nameservers == toset(["100.64.0.10", "100.64.0.11"])
    error_message = "An entry with literal nameservers must program exactly those."
  }
}

# The device's IPv4 is selected explicitly: the API's address ordering is
# convention, and an IPv6 nameserver breaks resolution for every tailnet client.
run "device_hostname_resolves_to_the_ipv4_address" {
  command = plan

  variables {
    split_dns = {
      "internal.example.com" = { device_hostname = "ts-dns" }
    }
  }

  assert {
    condition     = tailscale_dns_split_nameservers.this["internal.example.com"].nameservers == toset(["100.64.0.10"])
    error_message = "device_hostname must resolve to the device's single IPv4 (100.x) address, never its IPv6."
  }
}

# `one([])` is null rather than an error, so without the precondition an
# IPv6-only device programs `nameservers = [null]` instead of failing the plan.
run "a_device_with_no_ipv4_fails_the_plan" {
  command = plan

  variables {
    split_dns = {
      "internal.example.com" = { device_hostname = "ts-dns" }
    }
  }

  override_data {
    target = data.tailscale_device.split_dns["internal.example.com"]
    values = {
      addresses = ["fd7a:115c:a1e0::1"]
    }
  }

  expect_failures = [tailscale_dns_split_nameservers.this]
}

run "a_device_with_two_ipv4s_fails_the_plan" {
  command = plan

  variables {
    split_dns = {
      "internal.example.com" = { device_hostname = "ts-dns" }
    }
  }

  override_data {
    target = data.tailscale_device.split_dns["internal.example.com"]
    values = {
      addresses = ["100.64.0.10", "100.64.0.11"]
    }
  }

  expect_failures = [tailscale_dns_split_nameservers.this]
}
