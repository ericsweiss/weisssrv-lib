# The controller stays the source of truth for everything this provider cannot
# reach — policy ORDER, mDNS reflection, per-port native/tagged VLAN assignment,
# device adoption — so this module owns exactly the objects it declares and
# never a device. README "What this module cannot manage" is the list, and the
# consuming repo's runbook is where those steps live.

# `unifi_wlan.user_group_id` is Required with no default; the stock client QoS
# rate is the one every SSID here uses.
data "unifi_client_qos_rate" "default" {
  name = var.qos_rate_name
}

# Built-in zones are READ, never managed: v0.55.0 cannot import one by name (the
# fix landed after the tag), and a managed built-in would fight the controller
# over `network_ids`, which the provider replaces wholesale on every apply.
data "unifi_firewall_zone" "builtin" {
  for_each = var.builtin_zone_names

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
    # Not an input — `lifecycle` blocks take no variables, so this is fixed for
    # every consumer. Destroying a network drops every client on that VLAN, and
    # a renamed map key would plan exactly that. Stop managing one with
    # `terraform state rm 'module.<name>.unifi_network.this["<key>"]'` (the live
    # network is untouched), then delete the entry. README "Destroy protection".
    prevent_destroy = true
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

  # `for_each` rejects any value derived from a sensitive variable, and
  # var.wlans is sensitive because it carries passphrases. The SHAPE of a WLAN
  # is not a secret: splitting it out keeps for_each legal and keeps the plan
  # readable, while `passphrase` is read straight from var.wlans below and
  # therefore stays sensitive.
  wlans = nonsensitive({
    for key, w in var.wlans : key => {
      ssid                 = w.ssid
      network              = w.network
      wpa3                 = w.wpa3
      l2_isolation         = w.l2_isolation
      allow_2ghz_high_perf = w.allow_2ghz_high_perf
      hide                 = w.hide
    }
  })
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

    # merge() would let a custom zone shadow a built-in of the same key, and
    # every policy naming that zone would quietly point at the wrong one.
    precondition {
      condition     = !contains(keys(var.builtin_zone_names), each.key)
      error_message = "zones[\"${each.key}\"] collides with a builtin_zone_names key; policies resolve both from one namespace, so the names must be distinct."
    }
  }
}

resource "unifi_firewall_policy" "this" {
  for_each = local.policies

  name                 = each.value.name
  action               = each.value.action
  protocol             = each.value.protocol
  logging              = each.value.logging
  create_allow_respond = each.value.create_allow_respond

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
  }
}

resource "unifi_wlan" "this" {
  for_each = local.wlans

  name = each.value.ssid
  # PSK only, by design: this module has no RADIUS inputs, and `security` is a
  # Required attribute with no default.
  security      = "wpapsk"
  user_group_id = data.unifi_client_qos_rate.default.id
  network_id    = lookup(local.network_ids, each.value.network, "")
  passphrase    = var.wlans[each.key].passphrase

  # WPA3 is a modifier on wpapsk, not a `security` value, and PMF may not be
  # disabled while it is on.
  wpa3_support    = each.value.wpa3
  wpa3_transition = each.value.wpa3
  pmf_mode        = each.value.wpa3 ? "optional" : "disabled"

  # 6 GHz is not offered: including "6g" fails WLAN creation on this provider
  # (upstream #406). Enable the band in the UI, or wait for the fix and add it
  # here as an input then.
  wlan_bands = ["2g", "5g"]
  # UniFi's "connect high-performance clients to 5 GHz only" — inverted here so
  # the input reads as what it permits.
  no2ghz_oui   = !each.value.allow_2ghz_high_perf
  l2_isolation = each.value.l2_isolation
  hide_ssid    = each.value.hide

  # minimum_data_rate_*_kbps are deliberately unset: they are Computed, and
  # pinning them to a guess is how a 2.4 GHz IoT client stops associating.

  lifecycle {
    precondition {
      condition     = contains(keys(var.networks), each.value.network)
      error_message = "wlans[\"${each.key}\"].network names a key that is not in var.networks."
    }
  }
}

resource "unifi_client" "this" {
  for_each = var.clients

  mac        = each.value.mac
  name       = each.value.name
  note       = each.value.note
  fixed_ip   = each.value.fixed_ip
  network_id = each.value.network == null ? null : lookup(local.network_ids, each.value.network, "")

  # The client already exists the moment the controller sees the MAC; every
  # entry here adopts one rather than creating it.
  allow_existing = true

  lifecycle {
    precondition {
      condition     = each.value.network == null ? true : contains(keys(var.networks), each.value.network)
      error_message = "clients[\"${each.key}\"].network names a key that is not in var.networks."
    }
  }
}

resource "unifi_port_forward" "this" {
  for_each = var.port_forwards

  name     = each.key
  protocol = each.value.protocol

  wan = {
    interface = "wan"
    port      = each.value.wan_port
  }

  forward = {
    ip   = each.value.ip
    port = each.value.port
  }

  # `enabled` is Deprecated in 0.55.0 with no documented replacement — omitted
  # rather than pinned to a value the provider may stop sending.
}

# One site-wide settings object. Only the blocks declared here are read and
# written; `terraform destroy` drops the state entry and changes nothing on the
# controller (settings can be reset, never deleted), so it carries no
# prevent_destroy.
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

  igmp_snooping = {
    enabled = length(var.site_settings.igmp_snooping_networks) > 0
    network_ids = length(var.site_settings.igmp_snooping_networks) == 0 ? null : [
      for key in var.site_settings.igmp_snooping_networks : lookup(local.network_ids, key, "")
    ]
  }

  ips = {
    ips_mode = var.site_settings.ips_mode
  }

  lifecycle {
    precondition {
      condition = alltrue([
        for key in var.site_settings.igmp_snooping_networks : contains(keys(var.networks), key)
      ])
      error_message = "site_settings.igmp_snooping_networks names a key that is not in var.networks."
    }
  }
}
