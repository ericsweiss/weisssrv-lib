variable "networks" {
  description = <<-EOT
    Networks (VLANs) keyed by a stable identity string. The key is the state
    address AND the name every other input references (`zones`, `policies`,
    `wlans`, `clients`, `site_settings.igmp_snooping_networks`), so renaming one
    is a `moved {}` block — the resource carries `prevent_destroy`, which
    refuses the destroy half of a rename.

    `subnet` is written in GATEWAY form: the host part is the gateway address,
    so `10.0.30.1/24` is the network 10.0.30.0/24 with the gateway on .1. A
    network-address form (`10.0.30.0/24`) is rejected below — the controller
    would take .0 as the gateway.

    The controller's built-in Default network is keyed `default`, and that key
    is the ONLY entry allowed to omit `vlan`; every other entry needs one. The
    key is part of the module's contract, not a convention — reserving it is
    what makes a single forgotten `vlan` visible, since one untagged network is
    otherwise indistinguishable from the built-in.

    `dhcp.dns_servers` drives `dhcp_server.dns_enabled`: a non-empty list turns
    the DHCP DNS option on, an empty list leaves clients on the gateway's own
    resolver. An empty list is the only way to express "no DHCP DNS" — upstream
    #429 means an explicit `[]` written to the controller never converges, so
    the module sends null instead.

    Omitting `dhcp` entirely writes no `dhcp_server` block at all: the network
    is served by a relay or by another server, and the controller keeps whatever
    it already has.

    `dhcp.leasetime` is a Go duration string. Write the normalized `24h0m0s`
    form: the controller reads that form back, and a shorthand such as `24h`
    can round-trip as a standing diff.

    `dhcp.start` and `dhcp.stop` must lie inside this network's own `subnet`,
    and the pool must not cover the gateway address: a lease handing out the
    gateway takes the router off the segment. Both are checked here.
  EOT
  type = map(object({
    name = string
    vlan = optional(number)
    # CIDR in gateway form (host part = gateway address).
    subnet = string
    # `guest` sticks only inside the controller's own Hotspot zone; a guest VLAN
    # in a custom zone is `corporate` plus policies. `vlan-only` is rejected
    # below, because `subnet` is required here (README).
    purpose         = optional(string, "corporate")
    domain_name     = optional(string)
    internet_access = optional(bool, true)
    # Per-network IGMP snooping. On Network 10.3+ the effective toggle is the
    # site-level `site_settings.igmp_snooping_networks` list; this one stays for
    # older controllers.
    igmp_snooping = optional(bool, false)
    dhcp = optional(object({
      enabled     = optional(bool, true)
      start       = string
      stop        = string
      dns_servers = optional(list(string), [])
      leasetime   = optional(string, "24h0m0s")
    }))
  }))

  validation {
    condition = alltrue([
      for key, n in var.networks :
      can(regex("^[0-9]{1,3}(\\.[0-9]{1,3}){3}/[0-9]{1,2}$", n.subnet)) && can(cidrhost(n.subnet, 0))
    ])
    error_message = "networks[*].subnet must be an IPv4 CIDR, e.g. \"10.0.30.1/24\"."
  }

  # `10.0.30.0/24` applies cleanly and hands every DHCP client .0 as its
  # gateway.
  validation {
    condition = alltrue([
      for key, n in var.networks :
      can(cidrhost(n.subnet, 0)) ? split("/", n.subnet)[0] != cidrhost(n.subnet, 0) : true
    ])
    error_message = "networks[*].subnet must be in GATEWAY form — the host part is the gateway address, so write \"10.0.30.1/24\", not the network address \"10.0.30.0/24\"."
  }

  validation {
    condition = alltrue([
      for key, n in var.networks :
      n.vlan == null ? true : (n.vlan >= 1 && n.vlan <= 4094)
    ])
    error_message = "networks[*].vlan must be 1-4094 (omit it only for the controller's built-in Default network)."
  }

  # The vlan-uniqueness rule below skips nulls, so only the reserved `default`
  # key may omit `vlan`.
  validation {
    condition = alltrue([
      for key, n in var.networks : n.vlan != null || key == "default"
    ])
    error_message = "Only the `networks[\"default\"]` entry may omit `vlan` — that key is the controller's built-in Default network. Any other untagged entry shares the management network's wire instead of getting a segment of its own."
  }

  validation {
    condition = alltrue([
      for key, n in var.networks :
      contains(["corporate", "guest"], n.purpose)
    ])
    error_message = "networks[*].purpose must be corporate or guest. The provider's third value, vlan-only, is not modelled here: it is the shape with NO gateway (`third_party_gateway`), while this module makes `subnet` required and validates it in gateway form. Use corporate for a guest VLAN that lives in a custom firewall zone — the controller rewrites `guest` to `corporate` outside its own Hotspot zone and the apply then fails."
  }

  # The map key is the resource address, so nothing else objects to two entries
  # sharing a vlan id, a name or a range: the collision surfaces as a controller
  # error partway through the apply.
  validation {
    condition     = length([for n in var.networks : n.vlan if n.vlan != null]) == length(distinct([for n in var.networks : n.vlan if n.vlan != null]))
    error_message = "networks[*].vlan must be unique — two networks on one VLAN id is a controller error partway through the apply."
  }

  validation {
    condition     = length(var.networks) == length(distinct([for n in var.networks : n.name]))
    error_message = "networks[*].name must be unique — the name is what the controller UI and every `name=` import resolve against."
  }

  # CIDRs are disjoint or nested, so masking both to the SHORTER prefix is an
  # exact overlap test. An entry that failed the shape check above is skipped,
  # and the `j > i` arm compares each pair once.
  validation {
    condition = length(flatten([
      for i, a in values(var.networks) : [
        for j, b in values(var.networks) : "${a.name}/${b.name}"
        if j > i
        && can(cidrhost(a.subnet, 0))
        && can(cidrhost(b.subnet, 0))
        && cidrhost("${cidrhost(a.subnet, 0)}/${min(tonumber(split("/", a.subnet)[1]), tonumber(split("/", b.subnet)[1]))}", 0)
        == cidrhost("${cidrhost(b.subnet, 0)}/${min(tonumber(split("/", a.subnet)[1]), tonumber(split("/", b.subnet)[1]))}", 0)
      ]
    ])) == 0
    error_message = "networks[*].subnet must not overlap another entry's — two networks sharing addresses apply cleanly and then route each other's traffic, and an exact duplicate is a controller error partway through the apply."
  }

  validation {
    condition = alltrue([
      for key, n in var.networks :
      n.dhcp == null ? true : length(n.dhcp.dns_servers) <= 4
    ])
    error_message = "networks[*].dhcp.dns_servers takes at most 4 addresses (controller limit)."
  }

  # Regex fixes the shape, cidrhost the range: "10.0.30.999" is four octets and
  # not an address.
  validation {
    condition = alltrue(flatten([
      for key, n in var.networks : n.dhcp == null ? [true] : [
        for address in concat([n.dhcp.start, n.dhcp.stop], n.dhcp.dns_servers) :
        can(regex("^[0-9]{1,3}(\\.[0-9]{1,3}){3}$", address)) && can(cidrhost("${address}/32", 0))
      ]
    ]))
    error_message = "networks[*].dhcp start, stop and dns_servers must be bare IPv4 addresses with every octet in 0-255 — \"10.0.30.999\" has the right shape and is not an address."
  }

  # Ordering, as on the policy and port-forward ranges below. A pair that failed
  # the shape check above is skipped so `tonumber` is never reached.
  validation {
    condition = alltrue([
      for key, n in var.networks : n.dhcp == null ? true : (
        !alltrue([
          for address in [n.dhcp.start, n.dhcp.stop] :
          can(regex("^[0-9]{1,3}(\\.[0-9]{1,3}){3}$", address)) && can(cidrhost("${address}/32", 0))
          ]) ? true : (
          sum([for i, octet in split(".", n.dhcp.start) : tonumber(octet) * pow(256, 3 - i)])
          <= sum([for i, octet in split(".", n.dhcp.stop) : tonumber(octet) * pow(256, 3 - i)])
        )
      )
    ])
    error_message = "networks[*].dhcp.start must not be above .stop — a descending pool serves no leases."
  }

  # cidrhost masks an address to the prefix, so an address is inside a subnet
  # exactly when both mask to one network address. Entries that failed a shape
  # check above are skipped.
  validation {
    condition = alltrue(flatten([
      for key, n in var.networks : n.dhcp == null || !can(cidrhost(n.subnet, 0)) ? [true] : [
        for address in [n.dhcp.start, n.dhcp.stop] :
        !can(cidrhost("${address}/32", 0)) ? true : (
          cidrhost("${address}/${split("/", n.subnet)[1]}", 0) == cidrhost(n.subnet, 0)
        )
      ]
    ]))
    error_message = "networks[*].dhcp.start and .stop must lie inside that network's own `subnet` — the controller serves no lease from a pool outside the segment."
  }

  # The gateway is the host part of `subnet`. Inside the pool it goes out as a
  # lease and the segment loses its router.
  validation {
    condition = alltrue([
      for key, n in var.networks : n.dhcp == null ? true : (
        !alltrue([
          for address in [n.dhcp.start, n.dhcp.stop, split("/", n.subnet)[0]] :
          can(regex("^[0-9]{1,3}(\\.[0-9]{1,3}){3}$", address)) && can(cidrhost("${address}/32", 0))
          ]) ? true : (
          sum([for i, octet in split(".", split("/", n.subnet)[0]) : tonumber(octet) * pow(256, 3 - i)])
          < sum([for i, octet in split(".", n.dhcp.start) : tonumber(octet) * pow(256, 3 - i)])
          ||
          sum([for i, octet in split(".", split("/", n.subnet)[0]) : tonumber(octet) * pow(256, 3 - i)])
          > sum([for i, octet in split(".", n.dhcp.stop) : tonumber(octet) * pow(256, 3 - i)])
        )
      )
    ])
    error_message = "networks[*].dhcp must not cover the gateway address — the host part of `subnet` is the gateway, and a pool spanning it hands the router's address to a client."
  }

  # Shape only: the provider parses the value, so "24h" and "86400s" are legal.
  validation {
    condition = alltrue([
      for key, n in var.networks :
      n.dhcp == null ? true : (
        n.dhcp.leasetime != "" && can(regex("^([0-9]+h)?([0-9]+m)?([0-9]+s)?$", n.dhcp.leasetime))
      )
    ])
    error_message = "networks[*].dhcp.leasetime must be a Go duration string (\"24h0m0s\", \"24h\", \"86400s\")."
  }
}

variable "reserved_cidrs" {
  description = <<-EOT
    Ranges outside this controller's own networks that no managed network may
    overlap: the k3s pod and service CIDRs, and any neighbouring site's range.
    A VLAN overlapping one of them applies cleanly and then routes cluster
    traffic onto the wrong wire. The LAN is itself a `networks` entry, so the
    overlap rule on that variable already covers it.

    Checked on `unifi_network`, not here, because a variable validation cannot
    read another variable at this module's Terraform floor. Entries may be
    written in any form — they are compared as network addresses.
  EOT
  type        = list(string)
  default     = []

  validation {
    condition = alltrue([
      for cidr in var.reserved_cidrs :
      can(regex("^[0-9]{1,3}(\\.[0-9]{1,3}){3}/[0-9]{1,2}$", cidr)) && can(cidrhost(cidr, 0))
    ])
    error_message = "reserved_cidrs entries must be IPv4 CIDRs, e.g. \"10.42.0.0/16\"."
  }
}

variable "zones" {
  description = <<-EOT
    Custom firewall zones, keyed by the zone's DISPLAY NAME on the controller
    (the key is the state address and the name shown in the UI). Each value
    lists `networks` keys.

    Zone-per-network is the intended shape: inter-zone traffic is denied by
    default, so every allowance is an explicit `policies` entry and nothing is
    load-bearing on rule order.

    The key may not be a name the controller reserves for a built-in zone
    (`Internal`, `External`, `Gateway`, `Hotspot`, `Vpn`, `Dmz`, plus whatever
    `builtin_zone_names` declares), and every member network must be
    `corporate`: a `guest`-purpose network outside the controller's own Hotspot
    zone is rewritten to `corporate` and fails the apply. Both are checked on
    the resource, not here, because a variable validation cannot read another
    variable at this module's Terraform floor.

    Membership is a FULL REPLACEMENT on every apply — the provider always sends
    the whole `network_ids` list — and it is the only way to set a network's
    zone in v0.55.0 (`unifi_network` has no `firewall_zone_id`; upstream #417).
    Whether the controller also removes that network from `Internal` is
    controller behaviour the provider neither performs nor detects: probe it
    with `data.unifi_firewall_zone` after the first apply (README).
  EOT
  type = map(object({
    networks = list(string)
  }))
  default = {}

  # Two zones claiming one network never converge: each apply sends its own full
  # membership list, so whichever runs last wins and the loser reports drift on
  # every plan afterwards.
  validation {
    condition     = length(flatten([for z in var.zones : z.networks])) == length(distinct(flatten([for z in var.zones : z.networks])))
    error_message = "Each `networks` key may appear in at most one `zones` entry — membership is a full replacement on every apply, so a network claimed by two zones flips between them and policy evaluation is ambiguous."
  }

  # An empty custom zone applies cleanly and segments nothing — policies naming
  # it look like enforcement while matching no traffic.
  validation {
    condition     = alltrue([for z in var.zones : length(z.networks) > 0])
    error_message = "Every `zones` entry must list at least one `networks` key — an empty custom zone applies successfully but segments nothing."
  }
}

variable "builtin_zone_names" {
  description = <<-EOT
    Controller built-in zones to REFERENCE (never manage), keyed by the short
    name `policies[*].source.zone` / `.destination.zone` uses -> the zone's
    display name on the controller.

    They are read through `data.unifi_firewall_zone`, which is the only
    supported path in v0.55.0: importing a built-in zone by name is not in the
    pinned release (upstream PR #401), and managing one would fight the
    controller over its membership list.

    Display names are controller- and locale-dependent (`Internal`, `External`,
    `Gateway`, `Hotspot`, `Vpn`, `Dmz` on a UniFi OS 10.x console). Confirm them
    against the live controller before the first apply — a wrong name fails the
    data read with a lookup error, not a policy error.

    Only the entries a `policies` endpoint actually names are read, so an
    unreferenced default whose display name is wrong on this controller costs
    nothing: adding a key here is free until a policy uses it.
  EOT
  type        = map(string)
  default     = { internal = "Internal", external = "External", gateway = "Gateway" }
}

variable "policies" {
  description = <<-EOT
    Zone-based firewall policies. `name` is the state address, so it must be
    unique and renaming one replaces the policy.

    ORDER IS NOT MANAGED. `unifi_firewall_policy.index` is read-only in v0.55.0
    and the controller appends each new policy to the end of its zone-pair, so
    a rule that has to precede another is a UI step (README "What this module
    cannot manage"). The zone-per-network model keeps that from mattering: the
    entries here are allowances against a default deny, not a first-match list.

    `source.zone` / `destination.zone` name a `zones` key or a
    `builtin_zone_names` key — one namespace, resolved together.

    Set at most one of `ips` (literal addresses/CIDRs) or `networks`
    (`networks` keys) per endpoint; neither means any host in that zone. `port`
    is a string so lists and ranges work: "53", "80,443", "32410-32414", and it
    needs a port-bearing `protocol` (validated below).

    `create_allow_respond` writes the matching established/related return rule
    and defaults on, which is what an ALLOW almost always wants. It is honoured
    for `action = "ALLOW"` only: a BLOCK or REJECT always writes false, because
    the companion rule the controller would create is an ALLOW that Terraform
    never holds in state, never reports as drift, and that outlives the deny it
    was attached to. The controller also REJECTS it for `icmp`/`icmpv6`, so an
    `ALLOW` on those protocols must set it false (validated below) and pair
    with an explicit reverse policy. That validation is scoped to `ALLOW` for
    the same reason the derivation is: on a BLOCK or REJECT the attribute never
    reaches the controller, so an icmp deny left at the default is accepted and
    writes false.
  EOT
  type = list(object({
    name     = string
    action   = optional(string, "ALLOW")
    protocol = optional(string, "all")
    source = object({
      zone     = string
      ips      = optional(list(string))
      networks = optional(list(string))
      port     = optional(string)
    })
    destination = object({
      zone     = string
      ips      = optional(list(string))
      networks = optional(list(string))
      port     = optional(string)
    })
    create_allow_respond = optional(bool, true)
    logging              = optional(bool, false)
  }))
  default = []

  # for_each keys on `name`: a duplicate would silently drop a policy instead of
  # failing.
  validation {
    condition     = length(var.policies) == length(distinct([for p in var.policies : p.name]))
    error_message = "policies[*].name must be unique — the name is the resource key."
  }

  validation {
    condition = alltrue([
      for p in var.policies : contains(["ALLOW", "BLOCK", "REJECT"], p.action)
    ])
    error_message = "policies[*].action must be ALLOW, BLOCK or REJECT (upper case)."
  }

  validation {
    condition = alltrue([
      for p in var.policies :
      contains(["all", "tcp", "udp", "tcp_udp", "icmp", "icmpv6"], p.protocol)
    ])
    error_message = "policies[*].protocol must be one of all, tcp, udp, tcp_udp, icmp, icmpv6."
  }

  # FirewallPolicyCreateRespondTrafficPolicyNotAllowed, an apply-time 400.
  # Scoped to ALLOW because main.tf derives the attribute to false elsewhere.
  validation {
    condition = alltrue([
      for p in var.policies :
      contains(["icmp", "icmpv6"], p.protocol) && p.action == "ALLOW" ? !p.create_allow_respond : true
    ])
    error_message = "policies[*].create_allow_respond must be false for an ALLOW on protocol icmp/icmpv6 — the controller rejects the auto-created return rule. Write an explicit reverse policy instead."
  }

  # `matching_target` derives from which list is non-null, not from what is in
  # it, so an empty list stores a rule matching no host. Omitting both keys is
  # how "any host in that zone" is written.
  validation {
    condition = alltrue(flatten([
      for p in var.policies : [
        for endpoint in [p.source, p.destination] : (
          (endpoint.ips == null || endpoint.networks == null)
          && (endpoint.ips == null ? true : length(endpoint.ips) > 0)
          && (endpoint.networks == null ? true : length(endpoint.networks) > 0)
        )
      ]
    ]))
    error_message = "policies[*].source/destination may set one non-empty `ips` or `networks` list, not both — the provider derives one matching_target from whichever list is populated, so an empty one matches nothing and omitting both is how `any host in that zone` is written."
  }

  # A port on `all`/`icmp` has nothing to match: the controller either 400s the
  # apply or stores the policy with the port dropped, which is a rule far wider
  # than the one that was written.
  validation {
    condition = alltrue(flatten([
      for p in var.policies : [
        for endpoint in [p.source, p.destination] :
        endpoint.port == null ? true : contains(["tcp", "udp", "tcp_udp"], p.protocol)
      ]
    ]))
    error_message = "policies[*].source/destination.port requires protocol tcp, udp or tcp_udp — port matching is meaningless on `all`, `icmp` and `icmpv6`, and the controller drops the port rather than the rule."
  }

  # Same shapes port_forwards already validates: a typo here is an opaque
  # controller 400 during a supervised apply, after earlier policies landed.
  validation {
    condition = alltrue(flatten([
      for p in var.policies : [
        for endpoint in [p.source, p.destination] :
        endpoint.port == null ? true : can(regex("^[0-9]+(-[0-9]+)?(,[0-9]+(-[0-9]+)?)*$", endpoint.port))
      ]
    ]))
    error_message = "policies[*].source/destination.port must be a port, a range (\"32410-32414\") or a comma-separated list."
  }

  # Bounds and ordering; a value that failed the shape check above is skipped so
  # `tonumber` is never reached and the shape message stands alone.
  validation {
    condition = alltrue(flatten([
      for p in var.policies : [
        for endpoint in [p.source, p.destination] :
        endpoint.port == null || !can(regex("^[0-9]+(-[0-9]+)?(,[0-9]+(-[0-9]+)?)*$", endpoint.port)) ? true : alltrue([
          for segment in split(",", endpoint.port) : (
            alltrue([for bound in split("-", segment) : tonumber(bound) >= 1 && tonumber(bound) <= 65535])
            && (length(split("-", segment)) == 1 || tonumber(split("-", segment)[0]) <= tonumber(split("-", segment)[1]))
          )
        ])
      ]
    ]))
    error_message = "policies[*].source/destination.port must use ports in 1-65535, and a range must ascend — \"0\", \"70000\" and \"443-80\" all parse as a port list and match nothing on the controller."
  }

  validation {
    condition = alltrue(flatten([
      for p in var.policies : [
        for endpoint in [p.source, p.destination] :
        endpoint.ips == null ? [true] : [
          for address in endpoint.ips :
          can(regex("^[0-9]{1,3}(\\.[0-9]{1,3}){3}(/[0-9]{1,2})?$", address)) && can(cidrhost(strcontains(address, "/") ? address : "${address}/32", 0))
        ]
      ]
    ]))
    error_message = "policies[*].source/destination.ips must be bare IPv4 addresses or IPv4 CIDRs."
  }
}

variable "wlans" {
  description = <<-EOT
    WLANs keyed by identity string. The whole variable is sensitive because it
    carries `passphrase`, which this provider stores in state; only the
    passphrase keeps that mark inside the module, so plans still show the SSIDs.

    Every WLAN here is WPA-PSK: `security = "wpapsk"` is fixed, because the
    module has no RADIUS inputs.

    `bands` decides who owns the band set. Left unset (the default, null) the
    attribute is never written, so the CONSOLE owns it and a band enabled in
    the UI survives every apply. Set to a list it is terraform-owned and
    re-asserted on every apply, reverting a UI change. `6g` is allowed in that
    list, but including it fails WLAN creation on provider releases still
    carrying upstream #406 — so a 6 GHz SSID today is one that leaves `bands`
    unset and is toggled in the UI.

    `wpa3 = true` is WPA2/WPA3 transition mode with PMF optional. `false` is
    plain WPA2 with PMF disabled — what ESP32/Kasa-class gear needs.

    `allow_2ghz_high_perf = true` clears UniFi's "connect high-performance
    clients to 5 GHz only" (`no2ghz_oui`), i.e. it stops the AP steering capable
    clients off 2.4 GHz. It defaults FALSE, which is the controller's own
    default; set it true on an IoT SSID.
  EOT
  type = map(object({
    ssid    = string
    network = string
    # Optional in the TYPE only, so a later non-PSK `security` input is an
    # additive change; the validation below still requires it.
    passphrase = optional(string)
    wpa3       = optional(bool, true)
    # Client isolation: guests can reach the gateway and the internet, not each
    # other.
    l2_isolation         = optional(bool, false)
    allow_2ghz_high_perf = optional(bool, false)
    hide                 = optional(bool, false)
    # No default: an unset `bands` must stay null, which is what leaves the
    # attribute unwritten and the band set with the console.
    bands = optional(list(string))
  }))
  default   = {}
  sensitive = true

  # No value from the map reaches the message: error_message is rendered even
  # for a sensitive variable.
  validation {
    condition = alltrue([
      for key, w in var.wlans :
      w.passphrase == null ? false : can(regex("^[\\x20-\\x7e]{8,63}$", w.passphrase))
    ])
    error_message = "wlans[*].passphrase is required and must be 8-63 printable ASCII characters, which is the WPA-PSK rule (a smart quote or an accented letter pasted from a password manager is not one). An 1Password field that was renamed resolves to an empty string, and a sensitive value's diff hides it — the apply would silently reset the SSID's key."
  }

  # Same rule as above: the message names the field, never a value.
  validation {
    condition = alltrue([
      for key, w in var.wlans : length(w.bands) > 0 && alltrue([
        for band in w.bands : contains(["2g", "5g", "6g"], band)
      ])
      if w.bands != null
    ])
    error_message = "wlans[*].bands, when set, must be a non-empty list drawn from \"2g\", \"5g\" and \"6g\". Omit it to leave the band set to the console — an empty list is not that, it is a WLAN on no band at all."
  }
}

variable "qos_rate_name" {
  description = <<-EOT
    Name of the client QoS rate (the old "user group") every WLAN is assigned
    to. `unifi_wlan.user_group_id` is Required with no default, and it is read
    from this name through `data.unifi_client_qos_rate`; "Default" is the stock
    controller name.

    The lookup runs only when `wlans` is non-empty. The stock name is
    controller- and locale-dependent, so a gateway-only site would otherwise
    fail every plan on a rate it never assigns to anything.
  EOT
  type        = string
  default     = "Default"
}

variable "clients" {
  description = <<-EOT
    Fixed-IP client reservations keyed by identity string. A client exists on
    the controller as soon as it is seen on the wire, so an entry here ADOPTS
    the existing client (`allow_existing`) rather than creating one.

    `fixed_ip` needs the `network` it belongs to (validated below). It must lie
    inside that network's SUBNET and OUTSIDE its `dhcp` pool, both checked on
    `unifi_client`: a reservation inside the pool races the dynamic leases the
    controller hands out of the same range.

    Upstream #428: an in-place UPDATE of a client fails with "inconsistent
    result after apply: .last_ip". Change a name or an address with
    `terraform apply -replace='module.<name>.unifi_client.this["<key>"]'`.
  EOT
  type = map(object({
    mac      = string
    name     = string
    fixed_ip = optional(string)
    network  = optional(string)
    note     = optional(string, "Managed by Terraform")
  }))
  default = {}

  # Colon form only: the provider accepts dashes in config but REFUSES a
  # dash-separated MAC on import, and import is how an existing reservation is
  # adopted.
  validation {
    condition = alltrue([
      for key, c in var.clients :
      can(regex("^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$", c.mac))
    ])
    error_message = "clients[*].mac must be a colon-separated MAC (aa:bb:cc:dd:ee:ff) — `terraform import unifi_client` rejects any other separator."
  }

  # Shape then range, as on the DHCP bounds above: a reservation that is four
  # dotted octets but not an address is a controller error during the apply.
  validation {
    condition = alltrue([
      for key, c in var.clients :
      c.fixed_ip == null ? true : (
        can(regex("^[0-9]{1,3}(\\.[0-9]{1,3}){3}$", c.fixed_ip)) && can(cidrhost("${c.fixed_ip}/32", 0))
      )
    ])
    error_message = "clients[*].fixed_ip must be a bare IPv4 address with every octet in 0-255."
  }

  validation {
    condition = alltrue([
      for key, c in var.clients : c.fixed_ip == null || c.network != null
    ])
    error_message = "clients[*].fixed_ip requires `network` — the controller needs the network the reservation belongs to, and one whose address does not lie inside that network's subnet is never served."
  }

  # The MAC is the controller-side identity: two entries with one MAC are two
  # Terraform resources fighting over one object (and MACs compare
  # case-insensitively there).
  validation {
    condition     = length(var.clients) == length(distinct([for c in var.clients : lower(c.mac)]))
    error_message = "clients[*].mac must be unique ignoring case — two entries for one MAC would make multiple Terraform resources manage the same controller object."
  }

  # One address, one client — a duplicated reservation inside a network is an
  # address conflict the controller happily configures.
  validation {
    condition = (
      length([for c in var.clients : c if c.fixed_ip != null])
      == length(distinct([
        # Not coalesce(): it refuses an empty-string fallback when network is
        # null, which the fixed_ip-requires-network validation reports on its
        # own terms.
        for c in var.clients : "${c.network == null ? "" : c.network}:${c.fixed_ip}" if c.fixed_ip != null
      ]))
    )
    error_message = "clients[*].fixed_ip must be unique within each network — duplicate reservations assign one address to multiple clients."
  }
}

variable "port_forwards" {
  description = <<-EOT
    WAN port forwards keyed by identity string, which is also the forward's name
    in the UI. Ports are strings, so ranges and lists work ("32400",
    "32410-32414").

    `wan_interface` picks the WAN a forward lands on (`wan`, `wan2` or `both`)
    and defaults to the primary. Every forward accepts any source address:
    source restriction belongs in the firewall policies (and in the host's own
    firewall), not in a per-forward allowlist that nothing else can see.

    `logging` (default false) toggles the UniFi gateway's per-forward hit
    logging; enable it where the WAN-side connection log is wanted.
  EOT
  type = map(object({
    protocol      = optional(string, "tcp")
    wan_interface = optional(string, "wan")
    wan_port      = string
    ip            = string
    port          = string
    logging       = optional(bool, false)
  }))
  default = {}

  validation {
    condition = alltrue([
      for key, f in var.port_forwards : contains(["tcp", "udp", "tcp_udp"], f.protocol)
    ])
    error_message = "port_forwards[*].protocol must be tcp, udp or tcp_udp."
  }

  validation {
    condition = alltrue([
      for key, f in var.port_forwards : contains(["wan", "wan2", "both"], f.wan_interface)
    ])
    error_message = "port_forwards[*].wan_interface must be wan, wan2 or both — those are the interface names the provider accepts."
  }

  # Shape then range, as on the DHCP bounds and the client reservations: a
  # forward whose target is not an address is a WAN port that answers nothing.
  validation {
    condition = alltrue([
      for key, f in var.port_forwards :
      can(regex("^[0-9]{1,3}(\\.[0-9]{1,3}){3}$", f.ip)) && can(cidrhost("${f.ip}/32", 0))
    ])
    error_message = "port_forwards[*].ip must be a bare IPv4 address with every octet in 0-255 (the LAN target)."
  }

  validation {
    condition = alltrue(flatten([
      for key, f in var.port_forwards : [
        for port in [f.wan_port, f.port] : can(regex("^[0-9]+(-[0-9]+)?(,[0-9]+(-[0-9]+)?)*$", port))
      ]
    ]))
    error_message = "port_forwards[*].wan_port and .port must be a port, a range (\"32410-32414\") or a comma-separated list."
  }

  # Bounds and ordering, exactly as on the policy ports above: a forward on port
  # 0 or 70000 is a WAN rule the gateway can never match, and a descending range
  # forwards nothing at all.
  validation {
    condition = alltrue(flatten([
      for key, f in var.port_forwards : [
        for port in [f.wan_port, f.port] :
        !can(regex("^[0-9]+(-[0-9]+)?(,[0-9]+(-[0-9]+)?)*$", port)) ? true : alltrue([
          for segment in split(",", port) : (
            alltrue([for bound in split("-", segment) : tonumber(bound) >= 1 && tonumber(bound) <= 65535])
            && (length(split("-", segment)) == 1 || tonumber(split("-", segment)[0]) <= tonumber(split("-", segment)[1]))
          )
        ])
      ]
    ]))
    error_message = "port_forwards[*].wan_port and .port must use ports in 1-65535, and a range must ascend (\"32410-32414\", not \"32414-32410\")."
  }
}

variable "site_settings" {
  description = <<-EOT
    Site-wide controller settings. `unifi_setting` reads and writes ONLY the
    blocks declared here; every other setting on the site is left alone, and
    destroy is a state-only no-op (settings cannot be deleted, only reset).
    That is BLOCK granularity, not attribute granularity — the `mgmt` and `usg`
    blocks carry far more than the toggles below, and what keeps the rest intact
    is each attribute being Optional+Computed in the provider (main.tf).

    The defaults are the hardened posture: no unattended firmware upgrades (a
    gateway that reboots itself takes the whole site with it), no "network
    optimization" (it re-enables features behind your back), UPnP and NAT-PMP
    off (port forwards are declared, not requested by whatever is on the LAN),
    and IDS in detection-only mode (create-time intent only, see below).

    `ips_mode` is CREATE-TIME INTENT only: the resource `ignore_changes`es the
    whole `ips` block, because UniFi Network accepts the API write and then
    keeps its own value. It is asserted only on a site this module CREATES, so
    an adopted (imported) site never receives it, and changing it after the
    first apply plans nothing. Set day-2 IPS mode in the console (README,
    "Apply is supervised").

    `igmp_snooping_networks` lists `networks` keys; a non-empty list enables
    site IGMP snooping for exactly those networks. On Network 10.3+ this
    site-level setting — not `networks[*].igmp_snooping` — is the effective one.
    The EMPTY default leaves the toggle unmanaged: the `igmp_snooping` block is
    not written at all, so adopting this module never turns off snooping that
    was configured in the UI. Emptying the list again stops managing the toggle
    rather than disabling it — reset it in the console.
  EOT
  type = object({
    auto_upgrade           = optional(bool, false)
    network_optimization   = optional(bool, false)
    upnp                   = optional(bool, false)
    igmp_snooping_networks = optional(list(string), [])
    ips_mode               = optional(string, "ids")
  })
  default = {}

  validation {
    condition     = contains(["ids", "ips", "ipsInline", "disabled"], var.site_settings.ips_mode)
    error_message = "site_settings.ips_mode must be ids, ips, ipsInline or disabled (note the camel case on ipsInline)."
  }
}
