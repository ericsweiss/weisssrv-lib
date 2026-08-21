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
    # No `dhcp` block: DHCP is served elsewhere, so `dhcp_server` must plan as
    # null rather than as a scope with no addresses in it.
    transit = {
      name            = "Transit"
      vlan            = 50
      subnet          = "10.0.50.1/24"
      internet_access = false
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
    # Source-side match lists: both derivations live in the same expression as
    # the destination's, and a matching_target disagreeing with the populated
    # list is an apply-time 400 the plan cannot show.
    {
      name        = "iot-camera-to-nvr"
      protocol    = "tcp"
      source      = { zone = "iot", ips = ["10.0.30.3"], port = "554" }
      destination = { zone = "internal", ips = ["10.0.1.20"], port = "554" }
    },
    # A deny: `create_allow_respond` is derived, so this entry's default `true`
    # must NOT reach the controller as an auto-created reverse ALLOW.
    {
      name        = "guest-to-internal-block"
      action      = "BLOCK"
      logging     = true
      source      = { zone = "guest", networks = ["guest"] }
      destination = { zone = "internal" }
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
    # Name-only: no reservation, so `network_id` must plan as null rather than
    # as the sentinel the lookup falls back to.
    laptop = {
      mac  = "00:17:88:7E:C7:A3"
      name = "laptop"
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
    condition     = keys(unifi_network.this) == ["default", "guest", "iot", "transit"]
    error_message = "Every networks entry must plan one unifi_network."
  }

  assert {
    condition     = keys(unifi_firewall_zone.this) == ["guest", "iot"]
    error_message = "Custom zones are keyed by their display name; built-ins are data sources, never resources."
  }

  # `network_ids` is the ONLY way v0.55.0 puts a network into a custom zone, so
  # an empty list is a whole VLAN segmentation that plans and applies as a
  # no-op.
  assert {
    condition = (
      length(unifi_firewall_zone.this["iot"].network_ids) == 1
      && length(unifi_firewall_zone.this["guest"].network_ids) == 1
    )
    error_message = "Each zone must carry the network ids of exactly the `networks` keys it lists — an empty network_ids applies cleanly and segments nothing."
  }

  # Only the built-ins a policy names are read: an unreferenced default whose
  # display name is wrong on this controller would otherwise fail every plan.
  assert {
    condition     = keys(data.unifi_firewall_zone.builtin) == ["internal"]
    error_message = "Built-in zones must be read only when a policy endpoint names them."
  }

  assert {
    condition = (
      unifi_network.this["iot"].dhcp_server.start == "10.0.30.50"
      && unifi_network.this["iot"].dhcp_server.stop == "10.0.30.249"
    )
    error_message = "dhcp.start/stop must reach dhcp_server in that order — swapping them plans and applies without complaint."
  }

  assert {
    condition     = unifi_network.this["transit"].dhcp_server == null
    error_message = "A network with no `dhcp` block must plan no dhcp_server at all, not an empty scope."
  }

  assert {
    condition = (
      unifi_network.this["transit"].internet_access == false
      && unifi_network.this["guest"].internet_access == true
      && unifi_network.this["iot"].domain_name == "example.internal"
      && unifi_network.this["iot"].igmp_snooping == true
      && unifi_network.this["guest"].igmp_snooping == false
    )
    error_message = "internet_access, domain_name and the per-network igmp_snooping toggle must reach the network they were declared on."
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

  # The SOURCE endpoint derives from the same expression, and both of its
  # branches are as unforgiving at apply time as the destination's.
  assert {
    condition = (
      unifi_firewall_policy.this["iot-camera-to-nvr"].source.matching_target == "IP"
      && unifi_firewall_policy.this["iot-camera-to-nvr"].source.port_matching_type == "SPECIFIC"
      && unifi_firewall_policy.this["guest-to-internal-block"].source.matching_target == "NETWORK"
    )
    error_message = "A source endpoint must derive matching_target/port_matching_type from its own match list, not inherit the destination's."
  }

  assert {
    condition = (
      unifi_firewall_policy.this["guest-to-internal-block"].action == "BLOCK"
      && unifi_firewall_policy.this["guest-to-internal-block"].create_allow_respond == false
      && unifi_firewall_policy.this["guest-to-internal-block"].logging == true
      && unifi_firewall_policy.this["internal-to-iot"].create_allow_respond == true
    )
    error_message = "create_allow_respond is honoured for ALLOW only — a BLOCK/REJECT must write false, or the controller creates a reverse ALLOW that outlives the deny and never appears in state."
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

  # Guest client isolation is the reason the input exists, and it sits next to a
  # same-typed default-false neighbour that a swap would hide.
  assert {
    condition = (
      unifi_wlan.this["guest"].l2_isolation == true
      && unifi_wlan.this["guest"].hide_ssid == false
      && unifi_wlan.this["iot"].l2_isolation == false
    )
    error_message = "l2_isolation must land on the WLAN that declared it (guest client isolation), and `hide` must not be wired to it."
  }

  # No assertion on the `laptop` entry's fixed_ip/network_id: both attributes are
  # Optional+Computed, so an unset one is unknown at plan. It is in the fixture
  # to plan the null branch of the network_id lookup at all.
  assert {
    condition = (
      unifi_client.this["hue"].fixed_ip == "10.0.30.3"
      && unifi_client.this["hue"].allow_existing == true
    )
    error_message = "A client reservation must plan the fixed_ip it declared and adopt the existing client rather than creating one."
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

  # The positive half of the counted QoS lookup; the gateway-only run below is
  # the zero half. `user_group_id` is Required on every WLAN, so a site WITH
  # WLANs must still read the rate.
  assert {
    condition     = length(data.unifi_client_qos_rate.default) == 1
    error_message = "A site with WLANs must read the client QoS rate — `unifi_wlan.user_group_id` is Required with no default."
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

# The lower bound is its own comparison, and 0 is what a generator that treats
# "no VLAN" as a number rather than an omission emits.
run "rejects_a_vlan_of_zero" {
  command = plan

  variables {
    networks = {
      iot = {
        name   = "IoT"
        vlan   = 0
        subnet = "10.0.30.1/24"
      }
    }
  }

  expect_failures = [var.networks]
}

# The vlan-uniqueness check skips nulls, so a forgotten `vlan` on a second
# network is caught only here — and it applies cleanly, landing the network
# untagged next to the management one.
run "rejects_a_second_untagged_network" {
  command = plan

  variables {
    networks = {
      default = { name = "Default", subnet = "10.0.1.1/24" }
      iot     = { name = "IoT", subnet = "10.0.30.1/24" }
    }
  }

  expect_failures = [var.networks]
}

run "rejects_duplicate_network_vlans" {
  command = plan

  variables {
    networks = {
      iot     = { name = "IoT", vlan = 30, subnet = "10.0.30.1/24" }
      iot_dup = { name = "IoT copy", vlan = 30, subnet = "10.0.31.1/24" }
    }
  }

  expect_failures = [var.networks]
}

run "rejects_duplicate_network_names" {
  command = plan

  variables {
    networks = {
      iot     = { name = "IoT", vlan = 30, subnet = "10.0.30.1/24" }
      iot_dup = { name = "IoT", vlan = 31, subnet = "10.0.31.1/24" }
    }
  }

  expect_failures = [var.networks]
}

# vlan-only is the provider's third purpose and the one shape this module cannot
# express: `subnet` is required here and validated in gateway form.
run "rejects_the_vlan_only_purpose" {
  command = plan

  variables {
    networks = {
      transit = {
        name    = "Transit"
        vlan    = 50
        subnet  = "10.0.50.1/24"
        purpose = "vlan-only"
      }
    }
  }

  expect_failures = [var.networks]
}

run "rejects_a_malformed_dhcp_address" {
  command = plan

  variables {
    networks = {
      iot = {
        name   = "IoT"
        vlan   = 30
        subnet = "10.0.30.1/24"
        dhcp = {
          start = "10.0.30.50/24"
          stop  = "10.0.30.249"
        }
      }
    }
  }

  expect_failures = [var.networks]
}

# Four dotted decimal octets, and not an address: the shape regex passes it, so
# only the `cidrhost` half rejects it — before a supervised apply gets a
# controller error partway through.
run "rejects_an_out_of_range_dhcp_octet" {
  command = plan

  variables {
    networks = {
      iot = {
        name   = "IoT"
        vlan   = 30
        subnet = "10.0.30.1/24"
        dhcp = {
          start = "10.0.30.50"
          stop  = "10.0.30.999"
        }
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

# The key IS the name written to the controller, so the display-name spelling is
# the one an author reaching for a built-in would actually write.
run "rejects_a_zone_colliding_with_a_builtin_display_name" {
  command = plan

  variables {
    zones = {
      iot      = { networks = ["iot"] }
      Internal = { networks = ["default"] }
    }
  }

  expect_failures = [unifi_firewall_zone.this]
}

# Two zones each send their whole membership list, so a network in both never
# converges — the loser reports drift on every plan afterwards.
run "rejects_a_network_claimed_by_two_zones" {
  command = plan

  variables {
    zones = {
      iot   = { networks = ["iot"] }
      media = { networks = ["iot", "guest"] }
    }
  }

  expect_failures = [var.zones]
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

# A network belongs to exactly one zone, so an endpoint naming zone "iot" and
# network "guest" contradicts itself — the controller stores a rule matching
# nothing, or rejects it outright.
run "rejects_a_policy_network_outside_its_zone" {
  command = plan

  variables {
    policies = [
      {
        name        = "iot-to-guest"
        source      = { zone = "iot" }
        destination = { zone = "iot", networks = ["guest"] }
      },
    ]
  }

  expect_failures = [unifi_firewall_policy.this]
}

# The built-in half of the same rule, which cannot be checked positively (the
# zone's membership is a data read): a network this module has placed in a
# custom zone is no longer reachable through `internal`.
run "rejects_a_builtin_endpoint_naming_a_custom_zone_network" {
  command = plan

  variables {
    policies = [
      {
        name        = "internal-to-iot"
        source      = { zone = "internal", networks = ["iot"] }
        destination = { zone = "iot" }
      },
    ]
  }

  expect_failures = [unifi_firewall_policy.this]
}

# The other direction of both: a custom endpoint naming its OWN network, and a
# built-in endpoint naming a network that no custom zone claims (the management
# network stays in Internal), must both plan.
run "accepts_zone_consistent_network_endpoints" {
  command = plan

  variables {
    policies = [
      {
        name        = "iot-to-internal"
        source      = { zone = "iot", networks = ["iot"] }
        destination = { zone = "internal", networks = ["default"] }
      },
    ]
  }

  assert {
    condition = (
      unifi_firewall_policy.this["iot-to-internal"].source.matching_target == "NETWORK"
      && unifi_firewall_policy.this["iot-to-internal"].destination.matching_target == "NETWORK"
    )
    error_message = "An endpoint naming a network its own zone holds — and a built-in endpoint naming an unclaimed network — must both plan."
  }
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

run "rejects_create_allow_respond_on_icmpv6" {
  command = plan

  variables {
    policies = [
      {
        name        = "iot-ping6-homelab"
        protocol    = "icmpv6"
        source      = { zone = "iot" }
        destination = { zone = "internal" }
      },
    ]
  }

  expect_failures = [var.policies]
}

# The other direction of the same rule: the validation is scoped to ALLOW
# because main.tf derives the attribute, so an icmp deny left at the default
# `true` is accepted and still writes false. An explicit false on an icmp ALLOW
# is the shape a real ICMP pair uses.
run "accepts_icmp_denies_at_the_default_and_an_explicit_icmp_allow" {
  command = plan

  variables {
    policies = [
      {
        name        = "iot-ping-homelab-block"
        action      = "BLOCK"
        protocol    = "icmp"
        source      = { zone = "iot" }
        destination = { zone = "internal" }
      },
      {
        name        = "iot-ping6-homelab-reject"
        action      = "REJECT"
        protocol    = "icmpv6"
        source      = { zone = "iot" }
        destination = { zone = "internal" }
      },
      {
        name                 = "homelab-to-iot-icmp"
        protocol             = "icmp"
        create_allow_respond = false
        source               = { zone = "internal" }
        destination          = { zone = "iot" }
      },
    ]
  }

  assert {
    condition = (
      unifi_firewall_policy.this["iot-ping-homelab-block"].create_allow_respond == false
      && unifi_firewall_policy.this["iot-ping6-homelab-reject"].create_allow_respond == false
      && unifi_firewall_policy.this["homelab-to-iot-icmp"].create_allow_respond == false
    )
    error_message = "An icmp/icmpv6 deny must be accepted at the default and derive create_allow_respond = false, and an explicit false on an icmp ALLOW must pass validation."
  }
}

run "rejects_an_unknown_policy_protocol" {
  command = plan

  variables {
    policies = [
      {
        name        = "iot-to-dns"
        protocol    = "sctp"
        source      = { zone = "iot" }
        destination = { zone = "internal" }
      },
    ]
  }

  expect_failures = [var.policies]
}

# One keyword away from the README's own DNS example: without a port-bearing
# protocol the controller drops the port and keeps the rule.
run "rejects_a_port_on_a_portless_protocol" {
  command = plan

  variables {
    policies = [
      {
        name        = "iot-to-dns"
        source      = { zone = "iot" }
        destination = { zone = "internal", ips = ["10.0.1.150"], port = "53" }
      },
    ]
  }

  expect_failures = [var.policies]
}

run "rejects_a_malformed_policy_port" {
  command = plan

  variables {
    policies = [
      {
        name        = "iot-to-dns"
        protocol    = "tcp_udp"
        source      = { zone = "iot" }
        destination = { zone = "internal", ips = ["10.0.1.150"], port = "dns" }
      },
    ]
  }

  expect_failures = [var.policies]
}

# Port 0 parses as a port list and is not a port. Each of the three below
# reaches the controller as a rule that can never match.
run "rejects_a_zero_policy_port" {
  command = plan

  variables {
    policies = [
      {
        name        = "iot-to-dns"
        protocol    = "tcp_udp"
        source      = { zone = "iot" }
        destination = { zone = "internal", ips = ["10.0.1.150"], port = "0" }
      },
    ]
  }

  expect_failures = [var.policies]
}

run "rejects_a_policy_port_above_the_range" {
  command = plan

  variables {
    policies = [
      {
        name        = "iot-to-dns"
        protocol    = "tcp_udp"
        source      = { zone = "iot" }
        destination = { zone = "internal", ips = ["10.0.1.150"], port = "70000" }
      },
    ]
  }

  expect_failures = [var.policies]
}

# A descending range is the transposition an author makes copying two ports out
# of a runbook; the controller stores an empty range rather than complaining.
run "rejects_a_descending_policy_port_range" {
  command = plan

  variables {
    policies = [
      {
        name        = "iot-to-web"
        protocol    = "tcp"
        source      = { zone = "iot" }
        destination = { zone = "internal", ips = ["10.0.1.150"], port = "443-80" }
      },
    ]
  }

  expect_failures = [var.policies]
}

# The forms the site data actually uses must survive the bounds check: a comma
# list and an ascending range, on both a policy and a forward.
run "accepts_port_lists_and_ascending_ranges" {
  command = plan

  variables {
    policies = [
      {
        name        = "guest-to-gateway-dns"
        action      = "BLOCK"
        protocol    = "tcp_udp"
        source      = { zone = "guest" }
        destination = { zone = "gateway", port = "53,853" }
      },
      {
        name        = "iot-to-plex-discovery"
        protocol    = "tcp"
        source      = { zone = "iot" }
        destination = { zone = "internal", ips = ["10.0.1.20"], port = "32410-32414" }
      },
    ]
    port_forwards = {
      web = { wan_port = "8000-8100", ip = "10.0.1.100", port = "8000-8100" }
      alt = { wan_port = "80,443", ip = "10.0.1.100", port = "80,443" }
    }
  }

  assert {
    condition = (
      unifi_firewall_policy.this["guest-to-gateway-dns"].destination.port == "53,853"
      && unifi_port_forward.this["web"].wan.port == "8000-8100"
    )
    error_message = "A comma-separated list and an ascending range must pass the port bounds check on both policies and port forwards."
  }
}

run "rejects_a_malformed_policy_ip" {
  command = plan

  variables {
    policies = [
      {
        name        = "iot-to-dns"
        protocol    = "tcp_udp"
        source      = { zone = "iot" }
        destination = { zone = "internal", ips = ["10.0.1.150", "10.0.1.999"], port = "53" }
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

# matching_target derives from which list is NON-NULL, not from what is in it:
# an empty one derives IP/NETWORK with nothing to match, and the controller
# stores a rule that matches no host. Omitting the key is how "any" is written,
# so an empty list is never what the author meant.
run "rejects_an_empty_policy_ips_list" {
  command = plan

  variables {
    policies = [
      {
        name        = "iot-to-dns"
        protocol    = "tcp_udp"
        source      = { zone = "iot" }
        destination = { zone = "internal", ips = [], port = "53" }
      },
    ]
  }

  expect_failures = [var.policies]
}

run "rejects_an_empty_policy_networks_list" {
  command = plan

  variables {
    policies = [
      {
        name        = "internal-to-iot"
        source      = { zone = "internal" }
        destination = { zone = "iot", networks = [] }
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

# The upper bound is a separate comparison: 64 characters is one past WPA-PSK's
# limit and the kind of value a generated passphrase lands on.
run "rejects_a_long_passphrase" {
  command = plan

  variables {
    wlans = {
      iot = {
        ssid       = "example-iot"
        network    = "iot"
        passphrase = "0123456789012345678901234567890123456789012345678901234567890123"
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

run "rejects_a_malformed_client_fixed_ip" {
  command = plan

  variables {
    clients = {
      hue = {
        mac      = "00:17:88:7E:C7:A2"
        name     = "hue-bridge"
        fixed_ip = "10.0.30.3/32"
        network  = "iot"
      }
    }
  }

  expect_failures = [var.clients]
}

run "rejects_an_out_of_range_client_fixed_ip" {
  command = plan

  variables {
    clients = {
      hue = {
        mac      = "00:17:88:7E:C7:A2"
        name     = "hue-bridge"
        fixed_ip = "10.0.30.999"
        network  = "iot"
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

run "rejects_a_zero_port_forward_port" {
  command = plan

  variables {
    port_forwards = {
      https = { wan_port = "0", ip = "10.0.1.100", port = "443" }
    }
  }

  expect_failures = [var.port_forwards]
}

run "rejects_a_port_forward_port_above_the_range" {
  command = plan

  variables {
    port_forwards = {
      https = { wan_port = "443", ip = "10.0.1.100", port = "70000" }
    }
  }

  expect_failures = [var.port_forwards]
}

run "rejects_a_descending_port_forward_range" {
  command = plan

  variables {
    port_forwards = {
      plex = { wan_port = "32414-32410", ip = "10.0.1.152", port = "32414-32410" }
    }
  }

  expect_failures = [var.port_forwards]
}

run "rejects_a_malformed_port_forward_ip" {
  command = plan

  variables {
    port_forwards = {
      plex = { wan_port = "32400", ip = "plex.internal", port = "32400" }
    }
  }

  expect_failures = [var.port_forwards]
}

run "rejects_an_out_of_range_port_forward_ip" {
  command = plan

  variables {
    port_forwards = {
      plex = { wan_port = "32400", ip = "192.168.0.999", port = "32400" }
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

# A gateway-only site: no APs, no reservations, no forwards. `wlans` is
# sensitive and its default `{}` is the one value that runs `nonsensitive()`
# over an unmarked result, which hard-errors if the shape ever changes; the
# empty igmp list must write no settings block at all rather than an
# `enabled = false` that turns off snooping the console already had.
run "a_gateway_only_site_plans_clean" {
  command = plan

  variables {
    wlans         = {}
    clients       = {}
    port_forwards = {}
    site_settings = {}
  }

  assert {
    condition     = length(unifi_wlan.this) == 0 && length(unifi_client.this) == 0 && length(unifi_port_forward.this) == 0
    error_message = "Empty wlans/clients/port_forwards must plan no resources — and `wlans = {}` must survive the nonsensitive() unwrap."
  }

  # The QoS rate is looked up BY NAME, and the stock name is controller- and
  # locale-dependent: reading it unconditionally fails the whole plan on a site
  # that has no SSID to assign it to.
  assert {
    condition     = length(data.unifi_client_qos_rate.default) == 0
    error_message = "A site with no WLANs must skip the client QoS rate lookup entirely — a gateway-only site must not fail its plan on a localized rate name it never uses."
  }

  assert {
    condition     = unifi_setting.site.igmp_snooping == null
    error_message = "An empty igmp_snooping_networks must write NO igmp_snooping block — `enabled = false` would disable snooping a site had configured in the UI."
  }
}

run "rejects_an_empty_zone_membership" {
  command = plan

  variables {
    zones = {
      iot   = { networks = ["iot"] }
      empty = { networks = [] }
    }
  }

  expect_failures = [var.zones]
}

run "rejects_a_duplicate_client_mac" {
  command = plan

  variables {
    clients = {
      one = { mac = "AA:BB:CC:DD:EE:FF", name = "one" }
      two = { mac = "aa:bb:cc:dd:ee:ff", name = "two" }
    }
  }

  expect_failures = [var.clients]
}

run "rejects_a_duplicate_fixed_ip_in_one_network" {
  command = plan

  variables {
    clients = {
      one = { mac = "AA:BB:CC:DD:EE:01", name = "one", fixed_ip = "10.0.30.50", network = "iot" }
      two = { mac = "AA:BB:CC:DD:EE:02", name = "two", fixed_ip = "10.0.30.50", network = "iot" }
    }
  }

  expect_failures = [var.clients]
}
