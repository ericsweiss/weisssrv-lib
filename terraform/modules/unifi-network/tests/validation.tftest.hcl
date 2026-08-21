# `terraform validate` evaluates no caller values, so nothing else exercises the
# variable validations, the cross-map preconditions or the derived attributes
# (matching_target, dns_enabled, the no2ghz_oui inversion). Every run is
# `command = plan`: a plan creates no state, so the file needs no teardown —
# which the module's `prevent_destroy` resources would refuse anyway.
mock_provider "unifi" {}

variables {
  networks = {
    default = {
      name   = "Default"
      subnet = "10.0.1.1/24"
      dhcp = {
        start       = "10.0.1.100"
        stop        = "10.0.1.199"
        dns_servers = ["10.0.1.150", "10.0.1.160"]
      }
    }
    iot = {
      name          = "IoT"
      vlan          = 30
      subnet        = "10.0.30.1/24"
      domain_name   = "example.internal"
      igmp_snooping = true
      dhcp = {
        start       = "10.0.30.50"
        stop        = "10.0.30.249"
        dns_servers = ["10.0.1.150", "10.0.1.160"]
      }
    }
    guest = {
      name   = "Guest"
      vlan   = 40
      subnet = "10.0.40.1/24"
      dhcp = {
        start = "10.0.40.50"
        stop  = "10.0.40.249"
      }
    }
  }

  zones = {
    iot   = { networks = ["iot"] }
    guest = { networks = ["guest"] }
  }

  policies = [
    {
      name     = "iot-to-dns"
      protocol = "tcp_udp"
      source   = { zone = "iot" }
      destination = {
        zone = "internal"
        ips  = ["10.0.1.150", "10.0.1.160"]
        port = "53"
      }
    },
    {
      name        = "internal-to-iot"
      source      = { zone = "internal" }
      destination = { zone = "iot", networks = ["iot"] }
    },
  ]

  wlans = {
    iot = {
      ssid                 = "example-iot"
      network              = "iot"
      passphrase           = "iot-passphrase"
      wpa3                 = false
      allow_2ghz_high_perf = true
    }
    guest = {
      ssid         = "example-guest"
      network      = "guest"
      passphrase   = "guest-passphrase"
      l2_isolation = true
    }
  }

  clients = {
    hue = {
      mac      = "00:17:88:7E:C7:A2"
      name     = "hue-bridge"
      fixed_ip = "10.0.30.3"
      network  = "iot"
    }
  }

  port_forwards = {
    https = { wan_port = "443", ip = "10.0.1.100", port = "443" }
    wg    = { protocol = "udp", wan_port = "51820", ip = "10.0.1.99", port = "51820" }
  }

  site_settings = {
    igmp_snooping_networks = ["iot"]
  }
}

run "a_whole_site_plans_clean" {
  command = plan

  assert {
    condition     = keys(unifi_network.this) == ["default", "guest", "iot"]
    error_message = "Every networks entry must plan one unifi_network."
  }

  assert {
    condition     = keys(unifi_firewall_zone.this) == ["guest", "iot"]
    error_message = "Custom zones are keyed by their display name; built-ins are data sources, never resources."
  }

  # A DHCP DNS list is the only thing that turns the option on — an entry
  # without one must leave clients on the gateway's resolver, not send an empty
  # list the provider can never converge (#429).
  assert {
    condition     = unifi_network.this["iot"].dhcp_server.dns_enabled
    error_message = "A non-empty dhcp.dns_servers must set dhcp_server.dns_enabled."
  }

  assert {
    condition     = !unifi_network.this["guest"].dhcp_server.dns_enabled
    error_message = "An empty dhcp.dns_servers must leave dhcp_server.dns_enabled false."
  }

  assert {
    condition     = unifi_network.this["default"].vlan == null
    error_message = "A network with no vlan (the built-in Default) must plan without one."
  }

  # matching_target is derived from which match list is populated; a mismatch is
  # an apply-time 400 from the controller, invisible at plan.
  assert {
    condition = (
      unifi_firewall_policy.this["iot-to-dns"].source.matching_target == "ANY"
      && unifi_firewall_policy.this["iot-to-dns"].destination.matching_target == "IP"
      && unifi_firewall_policy.this["iot-to-dns"].destination.port_matching_type == "SPECIFIC"
    )
    error_message = "An endpoint with `ips` and a port must derive matching_target = IP and port_matching_type = SPECIFIC; one with neither must derive ANY."
  }

  # No assertion on the unset port_matching_type: it is Optional+Computed, so a
  # plan leaves it unknown and the controller assigns ANY.
  assert {
    condition     = unifi_firewall_policy.this["internal-to-iot"].destination.matching_target == "NETWORK"
    error_message = "An endpoint with `networks` must derive matching_target = NETWORK."
  }

  # The inversion is the whole point of the input name: allow_2ghz_high_perf
  # true means the AP must NOT steer capable clients off 2.4 GHz.
  assert {
    condition = (
      unifi_wlan.this["iot"].no2ghz_oui == false
      && unifi_wlan.this["guest"].no2ghz_oui == true
    )
    error_message = "allow_2ghz_high_perf must invert onto no2ghz_oui, and default to the controller's own posture (steer to 5 GHz)."
  }

  assert {
    condition = (
      unifi_wlan.this["iot"].pmf_mode == "disabled"
      && unifi_wlan.this["iot"].wpa3_support == false
      && unifi_wlan.this["guest"].pmf_mode == "optional"
      && unifi_wlan.this["guest"].wpa3_transition == true
    )
    error_message = "wpa3 = false must be plain WPA2 with PMF disabled; wpa3 = true must be transition mode with PMF optional (PMF cannot be disabled under WPA3)."
  }

  assert {
    condition     = unifi_wlan.this["guest"].wlan_bands == toset(["2g", "5g"])
    error_message = "WLANs are fixed to 2.4 + 5 GHz — including 6g fails WLAN creation on this provider (#406)."
  }

  assert {
    condition = (
      unifi_setting.site.igmp_snooping.enabled
      && length(unifi_setting.site.igmp_snooping.network_ids) == 1
    )
    error_message = "A non-empty igmp_snooping_networks must enable site IGMP snooping for exactly those networks."
  }

  assert {
    condition = (
      unifi_setting.site.mgmt.auto_upgrade == false
      && unifi_setting.site.usg.upnp_enabled == false
      && unifi_setting.site.usg.upnp_nat_pmp_enabled == false
      && unifi_setting.site.network_optimization.enabled == false
      && unifi_setting.site.ips.ips_mode == "ids"
    )
    error_message = "site_settings defaults must be the hardened posture: no auto-upgrade, no UPnP/NAT-PMP, no network optimization, IDS detection-only."
  }

  assert {
    condition     = unifi_port_forward.this["wg"].wan.interface == "wan" && unifi_port_forward.this["wg"].forward.port == "51820"
    error_message = "Port forwards must land on the primary WAN with the declared forward target."
  }
}

run "rejects_a_subnet_in_network_form" {
  command = plan

  variables {
    networks = {
      iot = {
        name   = "IoT"
        vlan   = 30
        subnet = "10.0.30.0/24"
      }
    }
  }

  expect_failures = [var.networks]
}

run "rejects_an_unparseable_subnet" {
  command = plan

  variables {
    networks = {
      iot = {
        name   = "IoT"
        vlan   = 30
        subnet = "10.0.30.1"
      }
    }
  }

  expect_failures = [var.networks]
}

run "rejects_a_vlan_outside_the_range" {
  command = plan

  variables {
    networks = {
      iot = {
        name   = "IoT"
        vlan   = 4095
        subnet = "10.0.30.1/24"
      }
    }
  }

  expect_failures = [var.networks]
}

run "rejects_an_unknown_network_purpose" {
  command = plan

  variables {
    networks = {
      iot = {
        name    = "IoT"
        vlan    = 30
        subnet  = "10.0.30.1/24"
        purpose = "hotspot"
      }
    }
  }

  expect_failures = [var.networks]
}

run "rejects_more_than_four_dhcp_dns_servers" {
  command = plan

  variables {
    networks = {
      iot = {
        name   = "IoT"
        vlan   = 30
        subnet = "10.0.30.1/24"
        dhcp = {
          start       = "10.0.30.50"
          stop        = "10.0.30.249"
          dns_servers = ["1.1.1.1", "1.0.0.1", "9.9.9.9", "8.8.8.8", "8.8.4.4"]
        }
      }
    }
  }

  expect_failures = [var.networks]
}

run "rejects_a_zone_naming_an_unknown_network" {
  command = plan

  variables {
    zones = {
      iot   = { networks = ["iot"] }
      guest = { networks = ["guests"] }
    }
  }

  expect_failures = [unifi_firewall_zone.this]
}

# A custom zone keyed like a built-in would shadow it in the merged lookup, and
# every policy naming that zone would silently point at the wrong one.
run "rejects_a_zone_colliding_with_a_builtin_key" {
  command = plan

  variables {
    zones = {
      iot      = { networks = ["iot"] }
      guest    = { networks = ["guest"] }
      internal = { networks = ["default"] }
    }
  }

  expect_failures = [unifi_firewall_zone.this]
}

run "rejects_a_policy_naming_an_unknown_zone" {
  command = plan

  variables {
    policies = [
      {
        name        = "iot-to-nowhere"
        source      = { zone = "iot" }
        destination = { zone = "dmz" }
      },
    ]
  }

  expect_failures = [unifi_firewall_policy.this]
}

run "rejects_a_policy_naming_an_unknown_network" {
  command = plan

  variables {
    policies = [
      {
        name        = "internal-to-iot"
        source      = { zone = "internal" }
        destination = { zone = "iot", networks = ["iiot"] }
      },
    ]
  }

  expect_failures = [unifi_firewall_policy.this]
}

run "rejects_duplicate_policy_names" {
  command = plan

  variables {
    policies = [
      {
        name        = "iot-to-dns"
        source      = { zone = "iot" }
        destination = { zone = "internal" }
      },
      {
        name        = "iot-to-dns"
        source      = { zone = "iot" }
        destination = { zone = "gateway" }
      },
    ]
  }

  expect_failures = [var.policies]
}

# The controller returns FirewallPolicyCreateRespondTrafficPolicyNotAllowed at
# apply time, long after the plan looked fine.
run "rejects_create_allow_respond_on_icmp" {
  command = plan

  variables {
    policies = [
      {
        name        = "iot-ping-homelab"
        protocol    = "icmp"
        source      = { zone = "iot" }
        destination = { zone = "internal" }
      },
    ]
  }

  expect_failures = [var.policies]
}

run "rejects_an_endpoint_matching_both_ips_and_networks" {
  command = plan

  variables {
    policies = [
      {
        name   = "iot-to-dns"
        source = { zone = "iot" }
        destination = {
          zone     = "internal"
          ips      = ["10.0.1.150"]
          networks = ["default"]
        }
      },
    ]
  }

  expect_failures = [var.policies]
}

run "rejects_an_unknown_policy_action" {
  command = plan

  variables {
    policies = [
      {
        name        = "iot-to-dns"
        action      = "allow"
        source      = { zone = "iot" }
        destination = { zone = "internal" }
      },
    ]
  }

  expect_failures = [var.policies]
}

run "rejects_a_short_passphrase" {
  command = plan

  variables {
    wlans = {
      iot = {
        ssid       = "example-iot"
        network    = "iot"
        passphrase = "short"
      }
    }
  }

  expect_failures = [var.wlans]
}

run "rejects_a_wlan_naming_an_unknown_network" {
  command = plan

  variables {
    wlans = {
      iot = {
        ssid       = "example-iot"
        network    = "iiot"
        passphrase = "iot-passphrase"
      }
    }
  }

  expect_failures = [unifi_wlan.this]
}

run "rejects_a_fixed_ip_without_a_network" {
  command = plan

  variables {
    clients = {
      hue = {
        mac      = "00:17:88:7E:C7:A2"
        name     = "hue-bridge"
        fixed_ip = "10.0.30.3"
      }
    }
  }

  expect_failures = [var.clients]
}

# Config accepts a dash-separated MAC; `terraform import unifi_client` does not,
# and import is how an existing reservation is adopted.
run "rejects_a_dash_separated_mac" {
  command = plan

  variables {
    clients = {
      hue = {
        mac  = "00-17-88-7E-C7-A2"
        name = "hue-bridge"
      }
    }
  }

  expect_failures = [var.clients]
}

run "rejects_a_client_naming_an_unknown_network" {
  command = plan

  variables {
    clients = {
      hue = {
        mac      = "00:17:88:7E:C7:A2"
        name     = "hue-bridge"
        fixed_ip = "10.0.30.3"
        network  = "iiot"
      }
    }
  }

  expect_failures = [unifi_client.this]
}

run "rejects_an_unknown_port_forward_protocol" {
  command = plan

  variables {
    port_forwards = {
      wg = { protocol = "udp/tcp", wan_port = "51820", ip = "10.0.1.99", port = "51820" }
    }
  }

  expect_failures = [var.port_forwards]
}

run "rejects_a_non_numeric_port_forward_port" {
  command = plan

  variables {
    port_forwards = {
      https = { wan_port = "443", ip = "10.0.1.100", port = "https" }
    }
  }

  expect_failures = [var.port_forwards]
}

run "rejects_an_unknown_ips_mode" {
  command = plan

  variables {
    site_settings = {
      ips_mode = "IPS"
    }
  }

  expect_failures = [var.site_settings]
}

run "rejects_igmp_snooping_on_an_unknown_network" {
  command = plan

  variables {
    site_settings = {
      igmp_snooping_networks = ["iiot"]
    }
  }

  expect_failures = [unifi_setting.site]
}
