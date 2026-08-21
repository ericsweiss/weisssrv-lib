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

    `vlan` is omitted for the controller's built-in Default network only; every
    other network needs one.

    `dhcp.dns_servers` drives `dhcp_server.dns_enabled`: a non-empty list turns
    the DHCP DNS option on, an empty list leaves clients on the gateway's own
    resolver. An empty list is the only way to express "no DHCP DNS" — upstream
    #429 means an explicit `[]` written to the controller never converges, so
    the module sends null instead.

    Omitting `dhcp` entirely writes no `dhcp_server` block at all: the network
    is served by a relay or by another server, and the controller keeps whatever
    it already has.

    Addresses are checked for SHAPE, never for containment: `dhcp.start`,
    `dhcp.stop` and `clients[*].fixed_ip` are not cross-checked against the
    subnet they belong to (README "What this module does not validate"). The
    controller rejects an out-of-subnet value at apply time.
  EOT
  type = map(object({
    name = string
    vlan = optional(number)
    # CIDR in gateway form (host part = gateway address).
    subnet = string
    # `guest` purpose only sticks while the network is in the controller's
    # guest/Hotspot zone; anywhere else the controller rewrites it to
    # `corporate` and the apply fails with an inconsistent-result error. A guest
    # VLAN in a CUSTOM zone is `corporate` plus policies (README).
    # `vlan-only` is rejected below: this module's `subnet` is required and
    # gateway-form validated, which is exactly what a vlan-only network has not
    # got.
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

  # The most expensive typo in this file: `10.0.30.0/24` applies cleanly and
  # hands every DHCP client .0 as its gateway.
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

  # The vlan-uniqueness check below skips nulls, so nothing else catches a
  # forgotten `vlan`. The controller has exactly one untagged network, and a
  # second entry without a tag does not fail: it lands UNTAGGED on the same wire
  # as the management network, sharing its broadcast domain while every zone,
  # policy and WLAN keyed to it reads as a segment of its own.
  validation {
    condition     = length([for n in var.networks : n if n.vlan == null]) <= 1
    error_message = "At most ONE `networks` entry may omit `vlan` — the controller's built-in Default network. A second untagged entry is almost always a forgotten `vlan`, and it shares the management network's untagged wire instead of getting a segment."
  }

  validation {
    condition = alltrue([
      for key, n in var.networks :
      contains(["corporate", "guest"], n.purpose)
    ])
    error_message = "networks[*].purpose must be corporate or guest. The provider's third value, vlan-only, is not modelled here: it is the shape with NO gateway (`third_party_gateway`), while this module makes `subnet` required and validates it in gateway form. Use corporate for a guest VLAN that lives in a custom firewall zone — the controller rewrites `guest` to `corporate` outside its own Hotspot zone and the apply then fails."
  }

  # The map key is the resource address, so Terraform has no reason to object to
  # two entries sharing a vlan id or a name: the collision surfaces as a
  # controller error partway through the apply, or as two same-named networks
  # that make every later lookup by name ambiguous.
  validation {
    condition     = length([for n in var.networks : n.vlan if n.vlan != null]) == length(distinct([for n in var.networks : n.vlan if n.vlan != null]))
    error_message = "networks[*].vlan must be unique — two networks on one VLAN id is a controller error partway through the apply."
  }

  validation {
    condition     = length(var.networks) == length(distinct([for n in var.networks : n.name]))
    error_message = "networks[*].name must be unique — the name is what the controller UI and every `name=` import resolve against."
  }

  validation {
    condition = alltrue([
      for key, n in var.networks :
      n.dhcp == null ? true : length(n.dhcp.dns_servers) <= 4
    ])
    error_message = "networks[*].dhcp.dns_servers takes at most 4 addresses (controller limit)."
  }

  # Both halves are needed and neither is redundant. The regex fixes the SHAPE —
  # four dotted decimal octets, so an IPv6 address or a CIDR is rejected here
  # and not by a parser that would happily accept it. `cidrhost` fixes the
  # RANGE, which no readable regex does: "10.0.30.999" is four dotted octets and
  # is not an address, and a DHCP scope bound the controller rejects is found at
  # apply time, partway through a supervised run.
  validation {
    condition = alltrue(flatten([
      for key, n in var.networks : n.dhcp == null ? [true] : [
        for address in concat([n.dhcp.start, n.dhcp.stop], n.dhcp.dns_servers) :
        can(regex("^[0-9]{1,3}(\\.[0-9]{1,3}){3}$", address)) && can(cidrhost("${address}/32", 0))
      ]
    ]))
    error_message = "networks[*].dhcp start, stop and dns_servers must be bare IPv4 addresses with every octet in 0-255 — \"10.0.30.999\" has the right shape and is not an address."
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
    supported path in v0.55.0: importing a built-in zone by name landed after
    the tag (upstream PR #401), and managing one would fight the controller over
    its membership list.

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

  # FirewallPolicyCreateRespondTrafficPolicyNotAllowed — an apply-time 400.
  # Scoped to ALLOW to match the derivation in main.tf: a BLOCK/REJECT always
  # writes false whatever the entry asked for, so the combination that can
  # actually reach the controller is ALLOW + icmp/icmpv6 + true.
  validation {
    condition = alltrue([
      for p in var.policies :
      contains(["icmp", "icmpv6"], p.protocol) && p.action == "ALLOW" ? !p.create_allow_respond : true
    ])
    error_message = "policies[*].create_allow_respond must be false for an ALLOW on protocol icmp/icmpv6 — the controller rejects the auto-created return rule. Write an explicit reverse policy instead."
  }

  # An EMPTY list is rejected alongside two populated ones, because
  # `matching_target` is derived from which list is non-null, not from what is
  # in it: `ips = []` derives IP with nothing to match, and the controller
  # stores a rule that matches no host — an allowance that silently does
  # nothing, or a BLOCK that silently blocks nothing. Omitting the key is how
  # "any host in that zone" is written.
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

  # The shape check says nothing about VALUE: "0", "70000" and the descending
  # "443-80" all parse as digits and separators. Each one reaches the controller
  # as a rule that can never match — a silent hole in an allowance, or a deny
  # that denies nothing — so the bounds are checked here.
  #
  # A value that failed the shape check above is skipped rather than re-reported,
  # so a non-numeric port gets the shape message and only that one. That skip is
  # also what keeps `tonumber` from erroring on a value the regex already
  # rejected.
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

    Every WLAN here is WPA-PSK on 2.4 + 5 GHz: `security = "wpapsk"` and
    `wlan_bands = ["2g","5g"]` are fixed, because including `6g` fails WLAN
    creation on this provider (upstream #406).

    `wpa3 = true` is WPA2/WPA3 transition mode with PMF optional. `false` is
    plain WPA2 with PMF disabled — what ESP32/Kasa-class gear needs.

    `allow_2ghz_high_perf = true` clears UniFi's "connect high-performance
    clients to 5 GHz only" (`no2ghz_oui`), i.e. it stops the AP steering capable
    clients off 2.4 GHz. It defaults FALSE, which is the controller's own
    default; set it true on an IoT SSID.
  EOT
  type = map(object({
    ssid       = string
    network    = string
    passphrase = string
    wpa3       = optional(bool, true)
    # Client isolation: guests can reach the gateway and the internet, not each
    # other.
    l2_isolation         = optional(bool, false)
    allow_2ghz_high_perf = optional(bool, false)
    hide                 = optional(bool, false)
  }))
  default   = {}
  sensitive = true

  # No value from the map reaches the message: error_message is rendered even
  # for a sensitive variable.
  validation {
    condition = alltrue([
      for key, w in var.wlans : length(w.passphrase) >= 8 && length(w.passphrase) <= 63
    ])
    error_message = "wlans[*].passphrase must be 8-63 characters (WPA-PSK). An 1Password field that was renamed resolves to an empty string, and a sensitive value's diff hides it — the apply would silently reset the SSID's key."
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
    inside that network's SUBNET, which is not checked here (README "What this
    module does not validate"); it does NOT have to lie inside the DHCP pool,
    and normally should not — reserving outside the pool is the standard way to
    keep a reservation from colliding with a dynamic lease.

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

    Every forward is on the primary `wan` interface and accepts any source
    address: source restriction belongs in the firewall policies (and in the
    host's own firewall), not in a per-forward allowlist that nothing else can
    see.
  EOT
  type = map(object({
    protocol = optional(string, "tcp")
    wan_port = string
    ip       = string
    port     = string
  }))
  default = {}

  validation {
    condition = alltrue([
      for key, f in var.port_forwards : contains(["tcp", "udp", "tcp_udp"], f.protocol)
    ])
    error_message = "port_forwards[*].protocol must be tcp, udp or tcp_udp."
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
    and IDS in detection-only mode.

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
