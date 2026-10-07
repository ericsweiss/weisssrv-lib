locals {
  policy_zones = distinct(flatten([
    for p in var.policies : [p.source.zone, p.destination.zone]
  ]))

  # `for_each` and `count` both reject a value derived from a sensitive
  # variable, so the WLAN shape is split out here; `passphrase` is read
  # straight from var.wlans below and keeps its mark.
  wlans = nonsensitive({
    for key, w in var.wlans : key => {
      ssid                 = w.ssid
      network              = w.network
      wpa3                 = w.wpa3
      l2_isolation         = w.l2_isolation
      allow_2ghz_high_perf = w.allow_2ghz_high_perf
      hide                 = w.hide
      bands                = w.bands
    }
  })

  # Only the built-ins a policy names are read: every entry is a data read on
  # every plan, and a display name wrong on this controller fails it.
  builtin_zones_used = {
    for key, name in var.builtin_zone_names : key => name
    if contains(local.policy_zones, key)
  }

  # Address arithmetic for the DHCP-pool collision precondition on
  # unifi_client, keyed only where there is something to compare.
  dhcp_pool_numbers = {
    for key, n in var.networks : key => {
      start = sum([for i, octet in split(".", n.dhcp.start) : tonumber(octet) * pow(256, 3 - i)])
      stop  = sum([for i, octet in split(".", n.dhcp.stop) : tonumber(octet) * pow(256, 3 - i)])
    } if n.dhcp != null
  }

  client_ip_numbers = {
    for key, c in var.clients : key => sum([
      for i, octet in split(".", c.fixed_ip) : tonumber(octet) * pow(256, 3 - i)
    ]) if c.fixed_ip != null
  }

  # CIDRs are disjoint or nested, so masking both to the SHORTER prefix is an
  # exact overlap test. Reported by the precondition on unifi_network below,
  # which names the colliding pair.
  reserved_overlaps = {
    for key, n in var.networks : key => [
      for cidr in var.reserved_cidrs : cidr
      if cidrhost("${cidrhost(n.subnet, 0)}/${min(tonumber(split("/", n.subnet)[1]), tonumber(split("/", cidr)[1]))}", 0)
      == cidrhost("${cidrhost(cidr, 0)}/${min(tonumber(split("/", n.subnet)[1]), tonumber(split("/", cidr)[1]))}", 0)
    ]
  }

  # Display names a custom zone may not take. The literal six are the English
  # names on a UniFi OS 10.x console, case-sensitive; a localised controller's
  # built-ins are covered by the `builtin_zone_names` half (README).
  reserved_zone_names = distinct(concat(
    values(var.builtin_zone_names),
    ["Internal", "External", "Gateway", "Hotspot", "Vpn", "Dmz"],
  ))
}

# `unifi_wlan.user_group_id` is Required with no default. Read only when a WLAN
# needs it: the lookup is by NAME, so an unconditional read fails every plan on
# a gateway-only site (README).
data "unifi_client_qos_rate" "default" {
  count = length(local.wlans) > 0 ? 1 : 0

  name = var.qos_rate_name
}

# Built-in zones are READ, never managed: v0.55.0 cannot import one by name, and
# a managed built-in would fight the controller over `network_ids`, which the
# provider replaces wholesale on every apply (README).
data "unifi_firewall_zone" "builtin" {
  for_each = local.builtin_zones_used

  name = each.value
}

resource "unifi_network" "this" {
  for_each = var.networks

  name = each.value.name
  vlan = each.value.vlan
  # Gateway form — the host part IS the gateway address (validated in
  # variables.tf).
  subnet          = each.value.subnet
  purpose         = each.value.purpose
  domain_name     = each.value.domain_name
  internet_access = each.value.internet_access
  igmp_snooping   = each.value.igmp_snooping

  # `multicast_dns` is deliberately unset: UniFi OS gateways ignore the write
  # and store false, so declaring it either churns the plan or lies about a
  # reflector that is actually a UI setting.

  # "manual": under the provider default "auto" the controller owns
  # dhcp_server.dns_enabled, domain_name and igmp_snooping and resets them on
  # every write (README "Apply is supervised").
  setting_preference = "manual"

  dhcp_server = each.value.dhcp == null ? null : {
    enabled     = each.value.dhcp.enabled
    start       = each.value.dhcp.start
    stop        = each.value.dhcp.stop
    dns_enabled = length(each.value.dhcp.dns_servers) > 0
    # null, not []: the provider reads "no DHCP DNS servers" back as null, so an
    # explicit empty list never converges (upstream #429).
    dns_servers = length(each.value.dhcp.dns_servers) > 0 ? each.value.dhcp.dns_servers : null
    leasetime   = each.value.dhcp.leasetime
  }

  lifecycle {
    # Destroying a network drops every client on that VLAN, and a renamed map
    # key plans exactly that. README "Destroy protection" has the two-step
    # removal.
    prevent_destroy = true

    precondition {
      condition     = length(local.reserved_overlaps[each.key]) == 0
      error_message = "networks[\"${each.key}\"].subnet ${each.value.subnet} overlaps reserved_cidrs ${join(", ", local.reserved_overlaps[each.key])} — a VLAN sharing the LAN, pod or service range routes cluster traffic onto the wrong wire."
    }
  }
}

locals {
  network_ids = { for key, n in unifi_network.this : key => n.id }

  # One namespace for `policies[*].source.zone`: custom zones by their key,
  # built-ins by the short name they are declared under.
  zone_ids = merge(
    { for key, z in unifi_firewall_zone.this : key => z.id },
    { for key, z in data.unifi_firewall_zone.builtin : key => z.id },
  )

  policies = { for p in var.policies : p.name => p }
}

resource "unifi_firewall_zone" "this" {
  for_each = var.zones

  # The map key is the zone's display name on the controller.
  name = each.key
  # `lookup` with a sentinel rather than a direct index, so a dangling key is
  # reported by the precondition below (which names the zone and the fix)
  # instead of Terraform's bare "Invalid index" against a resource address.
  network_ids = [for key in each.value.networks : lookup(local.network_ids, key, "")]

  lifecycle {
    # Destroying a zone silently returns its networks to the default zone, where
    # the inter-zone rules written here no longer apply — the segmentation is
    # gone but everything still routes. Same two-step removal as the networks.
    prevent_destroy = true

    precondition {
      condition = alltrue([
        for key in each.value.networks : contains(keys(var.networks), key)
      ])
      error_message = "zones[\"${each.key}\"].networks names a key that is not in var.networks."
    }

    # A `guest` purpose sticks only inside the controller's own Hotspot zone;
    # anywhere else the controller rewrites it and the apply fails partway
    # through. A key missing from var.networks is left to the precondition above.
    precondition {
      condition = alltrue([
        for key in each.value.networks :
        contains(keys(var.networks), key) ? var.networks[key].purpose == "corporate" : true
      ])
      error_message = "zones[\"${each.key}\"].networks may hold only `corporate` networks — the controller rewrites a `guest` purpose to `corporate` outside its own Hotspot zone, and the apply then fails partway through."
    }

    # Key namespace: a clash lets a custom zone shadow a built-in in the merged
    # lookup. The display-name half is the precondition below.
    precondition {
      condition     = !contains(keys(var.builtin_zone_names), each.key)
      error_message = "zones[\"${each.key}\"] collides with a `builtin_zone_names` key — policies resolve custom and built-in zones from one namespace, so the names must be distinct."
    }

    # The key IS the display name written to the controller, so `Internal`
    # here plans a second zone alongside the built-in one.
    precondition {
      condition     = !contains(local.reserved_zone_names, each.key)
      error_message = "zones[\"${each.key}\"] takes a name the controller reserves for a built-in zone. The key is written to the controller as the zone's display name, so it must not be one of ${join(", ", local.reserved_zone_names)}."
    }
  }
}

resource "unifi_firewall_policy" "this" {
  for_each = local.policies

  name     = each.value.name
  action   = each.value.action
  protocol = each.value.protocol
  logging  = each.value.logging
  # Derived: the controller's established/related companion is an ALLOW, so a
  # BLOCK or REJECT always writes false — otherwise the deny leaves an
  # allowance Terraform never holds in state.
  create_allow_respond = each.value.action == "ALLOW" ? each.value.create_allow_respond : false

  # `matching_target` is derived, never an input: the controller rejects a
  # policy whose target disagrees with the match list that is populated
  # (api.err.MissingFirewallPolicySourceMatchingTargetType).
  source = {
    zone_id         = lookup(local.zone_ids, each.value.source.zone, "")
    matching_target = each.value.source.ips != null ? "IP" : (each.value.source.networks != null ? "NETWORK" : "ANY")
    ips             = each.value.source.ips
    network_ids = each.value.source.networks == null ? null : [
      for key in each.value.source.networks : lookup(local.network_ids, key, "")
    ]
    port               = each.value.source.port
    port_matching_type = each.value.source.port == null ? null : "SPECIFIC"
  }

  destination = {
    zone_id         = lookup(local.zone_ids, each.value.destination.zone, "")
    matching_target = each.value.destination.ips != null ? "IP" : (each.value.destination.networks != null ? "NETWORK" : "ANY")
    ips             = each.value.destination.ips
    network_ids = each.value.destination.networks == null ? null : [
      for key in each.value.destination.networks : lookup(local.network_ids, key, "")
    ]
    port               = each.value.destination.port
    port_matching_type = each.value.destination.port == null ? null : "SPECIFIC"
  }

  # No prevent_destroy: a policy here is an ALLOWANCE against a default deny, so
  # removing one fails closed. The networks and zones it references do not.
  lifecycle {
    precondition {
      condition = (
        contains(keys(local.zone_ids), each.value.source.zone)
        && contains(keys(local.zone_ids), each.value.destination.zone)
      )
      error_message = "policies[\"${each.key}\"] names a zone that is neither a `zones` key nor a `builtin_zone_names` key."
    }

    precondition {
      condition = alltrue([
        for key in concat(
          coalesce(each.value.source.networks, []),
          coalesce(each.value.destination.networks, []),
        ) : contains(keys(var.networks), key)
      ])
      error_message = "policies[\"${each.key}\"] names a network key that is not in var.networks."
    }

    # A network belongs to exactly one zone, so `zone` and `networks` must
    # agree. A built-in zone's membership is a data read, so that half runs in
    # reverse: a network held by a custom zone is not reachable through it.
    precondition {
      condition = alltrue(flatten([
        for endpoint in [each.value.source, each.value.destination] : [
          for key in coalesce(endpoint.networks, []) :
          contains(keys(var.zones), endpoint.zone)
          ? contains(var.zones[endpoint.zone].networks, key)
          : !contains(flatten([for z in var.zones : z.networks]), key)
        ]
      ]))
      error_message = "policies[\"${each.key}\"] names a network that is not in the endpoint's own zone — a network belongs to exactly one zone, so `zone` and `networks` must agree (and a network held by a custom zone cannot be reached through a built-in one)."
    }
  }
}

resource "unifi_wlan" "this" {
  for_each = local.wlans

  name = each.value.ssid
  # PSK only, by design: this module has no RADIUS inputs, and `security` is a
  # Required attribute with no default.
  security = "wpapsk"
  # Index 0, not a splat: the data source is counted on this map being non-empty
  # (above), so inside this for_each it always exists.
  user_group_id = data.unifi_client_qos_rate.default[0].id
  network_id    = lookup(local.network_ids, each.value.network, "")
  passphrase    = var.wlans[each.key].passphrase

  # WPA3 is a modifier on wpapsk, not a `security` value, and PMF may not be
  # disabled while it is on.
  wpa3_support    = each.value.wpa3
  wpa3_transition = each.value.wpa3
  pmf_mode        = each.value.wpa3 ? "optional" : "disabled"

  # Optional+Computed: a null writes nothing and the console owns the band set;
  # a list is terraform-owned and re-asserted on every apply (README).
  wlan_bands = each.value.bands
  # UniFi's "connect high-performance clients to 5 GHz only" — inverted here so
  # the input reads as what it permits.
  no2ghz_oui   = !each.value.allow_2ghz_high_perf
  l2_isolation = each.value.l2_isolation
  hide_ssid    = each.value.hide

  # minimum_data_rate_*_kbps are deliberately unset: they are Computed, and
  # pinning them to a guess is how a 2.4 GHz IoT client stops associating.

  lifecycle {
    # Both are console-owned and read back on every WLAN write; re-planning
    # either ends the apply in "inconsistent result" or reverts an operator's
    # setting (README "What this module cannot manage").
    ignore_changes = [
      ap_group_ids,
      minrate_setting_preference,
    ]

    precondition {
      condition     = contains(keys(var.networks), each.value.network)
      error_message = "wlans[\"${each.key}\"].network names a key that is not in var.networks."
    }
  }
}

resource "unifi_client" "this" {
  for_each = var.clients

  mac      = each.value.mac
  name     = each.value.name
  note     = each.value.note
  fixed_ip = each.value.fixed_ip
  # The controller rejects a virtual-network override on the default network
  # (api.err.VirtualNetworkOverrideUnsupportedForDefaultNetwork), so a client
  # there is written as a bare fixed-IP reservation.
  network_id = (each.value.network == null || each.value.network == "default") ? null : lookup(local.network_ids, each.value.network, "")

  # The client already exists the moment the controller sees the MAC; every
  # entry here adopts one rather than creating it.
  allow_existing = true

  lifecycle {
    precondition {
      condition     = each.value.network == null ? true : contains(keys(var.networks), each.value.network)
      error_message = "clients[\"${each.key}\"].network names a key that is not in var.networks."
    }

    # Containment by the cidrhost mask, as on the DHCP bounds. A key missing
    # from var.networks is left to the precondition above.
    precondition {
      condition = (each.value.fixed_ip == null || !contains(keys(var.networks), each.value.network == null ? "" : each.value.network)) ? true : (
        cidrhost("${each.value.fixed_ip}/${split("/", var.networks[each.value.network].subnet)[1]}", 0)
        == cidrhost(var.networks[each.value.network].subnet, 0)
      )
      error_message = "clients[\"${each.key}\"].fixed_ip is outside that client's own networks[\"${each.value.network == null ? "" : each.value.network}\"].subnet — the controller never serves a reservation from another segment."
    }

    # The controller allocates dynamic leases out of the same range, so a
    # reservation inside the pool is an address conflict waiting on a lease.
    precondition {
      condition = (
        each.value.fixed_ip == null
        || !contains(keys(var.networks), each.value.network == null ? "" : each.value.network)
        || var.networks[each.value.network].dhcp == null
        ) ? true : (
        local.client_ip_numbers[each.key] < local.dhcp_pool_numbers[each.value.network].start
        || local.client_ip_numbers[each.key] > local.dhcp_pool_numbers[each.value.network].stop
      )
      error_message = "clients[\"${each.key}\"].fixed_ip falls inside the networks[\"${each.value.network == null ? "" : each.value.network}\"] DHCP pool — reserve outside the pool, or move the pool, so the controller cannot lease the address to something else."
    }
  }
}

resource "unifi_port_forward" "this" {
  for_each = var.port_forwards

  name     = each.key
  protocol = each.value.protocol

  wan = {
    interface = each.value.wan_interface
    port      = each.value.wan_port
  }

  forward = {
    ip   = each.value.ip
    port = each.value.port
  }

  # WAN-side per-forward hit logging (the "Log" toggle in the UI); default off.
  logging = each.value.logging

  # `enabled` is Deprecated in 0.55.0 with no documented replacement — omitted
  # rather than pinned to a value the provider may stop sending.
}

# One site-wide settings object; only declared blocks are written, and destroy
# is a state-only no-op. Undeclared mgmt/usg attributes survive only because
# they are Optional+Computed — plan-review the first apply (README).
resource "unifi_setting" "site" {
  mgmt = {
    auto_upgrade = var.site_settings.auto_upgrade
  }

  network_optimization = {
    enabled = var.site_settings.network_optimization
  }

  # UPnP lives on the usg block, not on a settings resource of its own.
  usg = {
    upnp_enabled         = var.site_settings.upnp
    upnp_nat_pmp_enabled = var.site_settings.upnp
  }

  # An empty list writes NO block, so adopting this module never turns off
  # snooping the console had configured; emptying the list later stops managing
  # the toggle rather than disabling it (variables.tf).
  igmp_snooping = length(var.site_settings.igmp_snooping_networks) == 0 ? null : {
    enabled = true
    network_ids = [
      for key in var.site_settings.igmp_snooping_networks : lookup(local.network_ids, key, "")
    ]
  }

  # `ips_mode` only: the signature categories and inspected networks are curated
  # in the console, and an IDS with neither selected detects nothing (README).
  ips = {
    ips_mode = var.site_settings.ips_mode
  }

  lifecycle {
    # `ips` is create-time intent only: the controller keeps its own value and
    # re-planning the write errors "inconsistent result". Day-2 IPS mode is
    # console-owned (README "Apply is supervised").
    ignore_changes = [ips]

    precondition {
      condition = alltrue([
        for key in var.site_settings.igmp_snooping_networks : contains(keys(var.networks), key)
      ])
      error_message = "site_settings.igmp_snooping_networks names a key that is not in var.networks."
    }
  }
}
