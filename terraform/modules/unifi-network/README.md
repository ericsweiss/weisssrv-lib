# unifi-network

A UniFi site's networks, firewall zones and zone-based policies, WLANs, client
reservations, port forwards and hardened site settings — as code, against the
`ubiquiti-community/unifi` provider.

The module is the **shape**; the VLAN/zone/policy inventory is site data the
caller supplies (a cluster instance's `terraform/unifi` root). It manages
objects only: no device is adopted, configured or port-overridden here — see
"What this module cannot manage".

## Consuming it

The tag below is an example: use the tag your repo pins (docs/VERSIONING.md).

```hcl
module "network" {
  source = "git::https://git.ericsweiss.com/eric/weisssrv-lib.git//terraform/modules/unifi-network?ref=v0.13.0"

  networks = {
    # `subnet` is GATEWAY form: the host part is the gateway address.
    default = {
      name   = "Default"
      subnet = "10.0.1.1/24"
      dhcp   = { start = "10.0.1.100", stop = "10.0.1.199", dns_servers = local.dns_ips }
    }
    iot = {
      name          = "IoT"
      vlan          = 30
      subnet        = "10.0.30.1/24"
      domain_name   = "example.internal"
      igmp_snooping = true
      dhcp          = { start = "10.0.30.50", stop = "10.0.30.249", dns_servers = local.dns_ips }
    }
  }

  # One zone per network: inter-zone traffic is denied by default, so every
  # allowance below is explicit and nothing depends on rule order.
  zones = {
    iot = { networks = ["iot"] }
  }

  policies = [
    {
      name     = "iot-to-dns"
      protocol = "tcp_udp"
      source   = { zone = "iot" }
      destination = {
        zone = "internal" # a builtin_zone_names key
        ips  = local.dns_ips
        port = "53"
      }
    },
  ]

  wlans = {
    iot = {
      ssid                 = "example-iot"
      network              = "iot"
      passphrase           = var.wlan_passphrase_iot
      wpa3                 = false # ESP32-class gear: plain WPA2, PMF off
      allow_2ghz_high_perf = true  # do not steer capable clients off 2.4 GHz
    }
  }

  clients = {
    hue = { mac = "00:17:88:7e:c7:a2", name = "hue-bridge", fixed_ip = "10.0.30.3", network = "iot" }
  }

  port_forwards = {
    wg = { protocol = "udp", wan_port = "51820", ip = "10.0.1.99", port = "51820" }
  }

  site_settings = {
    igmp_snooping_networks = ["iot"]
  }
}
```

The provider and backend are the **root module's** job — a reusable module can
declare neither:

```hcl
provider "unifi" {
  api_url = var.unifi_api_url # https://<gateway>, no /api path
  api_key = var.unifi_api_key # UniFi OS -> Control Plane -> Integrations -> API Key
  # The console serves its own self-signed certificate on the LAN address.
  allow_insecure = true
}

terraform {
  backend "http" {} # configured via TF_HTTP_* (ci/templates/terraform-http-backend.yml)
}
```

`unifi_api_key` and every WLAN passphrase are injected as `TF_VAR_*` from
1Password at apply time (`op run`), never defaulted and never committed. A key
that resolves to an empty string authenticates as nobody and the plan fails at
the first data read — declare the variables `sensitive` with a non-emptiness
validation in the root, so the failure names the missing item.

## Inputs

| Input | Type | Default | Notes |
|---|---|---|---|
| `networks` | map(object) | — | Keyed by identity string; the key is what every other input references. `subnet` is gateway form (`10.0.30.1/24`), validated against the network-address form. `vlan` omitted only for the built-in Default — at most ONE entry may omit it, and vlan ids and names must be unique. `purpose` is `corporate` or `guest` — `vlan-only` is rejected (`subnet` is required here). `dhcp = {enabled, start, stop, dns_servers, leasetime}`; a non-empty `dns_servers` sets `dns_enabled`, and omitting `dhcp` writes no `dhcp_server` at all. |
| `zones` | map(object) | `{}` | Custom firewall zones keyed by DISPLAY NAME; `networks` lists `networks` keys, each of which may appear in at most one zone. Membership is a full replacement on every apply. |
| `builtin_zone_names` | map(string) | `{internal="Internal", external="External", gateway="Gateway"}` | Short name → the controller's display name. Only the entries a policy endpoint actually names are read, so an unused one costs nothing; confirm the display names of the ones you use against the live controller. |
| `policies` | list(object) | `[]` | `name` (unique — it is the resource key), `action` (`ALLOW`), `protocol` (`all`), `source`/`destination` `{zone, ips, networks, port}`, `create_allow_respond` (`true`, honoured for `ALLOW` only), `logging`. A `port` requires a tcp/udp/tcp_udp `protocol`. An endpoint sets at most one of `ips`/`networks` and neither may be empty — omit both for "any host in that zone", and any `networks` it does name must belong to that endpoint's own zone. `port` takes 1-65535, ascending ranges only. `zone` resolves against `zones` **and** `builtin_zone_names`. |
| `wlans` | map(object) | `{}` | **sensitive.** `{ssid, network, passphrase, wpa3, l2_isolation, allow_2ghz_high_perf, hide}`. `security = "wpapsk"` and `wlan_bands = ["2g","5g"]` are fixed. |
| `qos_rate_name` | string | `"Default"` | Client QoS rate (old "user group") every WLAN is assigned to; `unifi_wlan.user_group_id` is Required with no default. Read only when `wlans` is non-empty, so a gateway-only site never fails a plan on a rate name it does not use. |
| `clients` | map(object) | `{}` | `{mac (colon form), name, fixed_ip, network, note}`. `fixed_ip` requires `network` and must lie inside that network's SUBNET — not inside its DHCP pool, and reserving outside the pool is the normal way to avoid colliding with a dynamic lease. |
| `port_forwards` | map(object) | `{}` | `{protocol, wan_port, ip, port}`; ports are strings, so ranges and lists work — each port 1-65535, each range ascending. Primary WAN, any source. |
| `site_settings` | object | hardened baseline | `auto_upgrade=false`, `network_optimization=false`, `upnp=false` (also NAT-PMP), `ips_mode="ids"`, `igmp_snooping_networks=[]` — the empty list leaves the site's IGMP-snooping toggle **unmanaged**, see below. |

Two input names deliberately do not match the provider attribute they drive:

- **`wlans[*].allow_2ghz_high_perf`** is the inverse of `no2ghz_oui`. UniFi's
  toggle is "connect high-performance clients to 5 GHz only"; the input says
  what it *permits*, and defaults `false` so an unset WLAN keeps the
  controller's own posture. Set it `true` on an IoT SSID.
- **`site_settings.upnp`** drives `usg.upnp_enabled` *and*
  `usg.upnp_nat_pmp_enabled`: leaving NAT-PMP on while UPnP is off still lets a
  LAN host punch its own hole.

`site_settings.igmp_snooping_networks` is opt-in, not a toggle: a non-empty list
writes the `igmp_snooping` block and enables snooping for exactly those
networks, and the empty default writes **no block at all**, so adopting this
module never turns off snooping a site configured in the UI. Emptying the list
again stops managing the setting rather than disabling it — reset it in the
console if that is what you meant.

## What this module does not validate

Every address input is checked for SHAPE (bare IPv4, IPv4 CIDR, gateway-form
subnet) and for octet RANGE — `10.0.30.999` is four dotted octets and is
rejected — but never for CONTAINMENT. `networks[*].dhcp.start`/`.stop` and
`clients[*].fixed_ip` are not cross-checked against the subnet they belong to:
Terraform has no `cidrcontains`, and the arithmetic that approximates it is
worse than nothing when it is subtly wrong on a file that writes a gateway's
segmentation. The controller rejects an out-of-subnet value at apply time, and a
renumber is the case to re-read by hand — changing a `subnet` means changing its
pool and its reservations in the same edit.

IPv6 is not configured, and that is a posture, not an omission: no input touches
any `ipv6_*` attribute, so every managed network keeps the provider default
`ipv6_interface_type = "none"` and hands out no GUA. A site that wants IPv6 has
to add the v6 attributes here *and* the v6 counterparts of every allowlist below
the gateway (host firewall sets, ingress allowlists, network policies) in the
same change — an IPv4-only allowlist under a v6-capable client is an open door
that no plan shows.

## Outputs

`network_ids`, `zone_ids` (custom zone keys **and** the built-in keys the
policies reference, in the one namespace policies resolve against), `wlan_ids`.

## What this module cannot manage

The provider's own limits at v0.55.0. Everything here is a UI step or a runbook
step in the consuming repo — none of it is drift this module will report.

| Not managed | Why |
|---|---|
| **Policy ORDER** | `unifi_firewall_policy.index` is read-only; the controller appends every new policy to the end of its zone-pair and the Integration API exposes no reorder (upstream #407). The zone-per-network model is what makes that safe: entries are allowances against a default deny, not a first-match list. |
| **mDNS reflection** | `unifi_network.multicast_dns` is ignored by UniFi OS gateways, which always store `false`. The module leaves it unset rather than planning a lie; enable the reflector per network in the UI. |
| **Devices, ports, native/tagged VLAN per port** | `unifi_device` cannot create anything (adoption only), and its `port_override` block is unsafe at 0.55.0: zero blocks wipes live overrides (#438), a `op_mode: switch` port strips fields from every port on the device (#430), and unset Optional+Computed attributes fail the apply (#431). Port layout is a documented physical map, not code. |
| **6 GHz** | Including `6g` in `wlan_bands` fails WLAN creation (#406). |
| **Per-SSID band steering** | `bandsteering_mode` is a device attribute, not a WLAN one (#388). `allow_2ghz_high_perf` is the closest per-SSID control. |
| **A network's zone from the network side** | `unifi_network` has no `firewall_zone_id` (#417); zone membership is set only from `zones`. |
| **Built-in zones as resources** | v0.55.0 cannot import one by name, and managing one would fight the controller for membership. They are read through `data.unifi_firewall_zone`. |

Two behaviours to verify on the controller rather than assume:

- **Does a custom zone take its network out of `Internal`?** The provider issues
  exactly one API call, for the zone it manages: it neither moves nor detects
  anything else. After the first custom-zone apply, read the built-in zone's
  membership and check the network left it — from the caller, that data source
  is inside the module:

  ```bash
  terraform state show 'module.network.data.unifi_firewall_zone.builtin["internal"]'
  ```

  If the network did not leave, it sits in two zones and policy evaluation is
  ambiguous. (The lookup key is a `builtin_zone_names` key, and only the keys a
  policy references are read at all.)

  Because that answer is unverified, a policy endpoint may not name a network
  through a zone that does not hold it: a `networks` list on a custom-zone
  endpoint must be that zone's own membership, and one on a **built-in**
  endpoint may not name a network this module has placed in a custom zone. The
  module refuses the contradiction rather than betting on which way the
  controller resolves it.
- **The built-in zone display names.** `Internal` / `External` / `Gateway` are
  the defaults here; capitalisation matters and localised controllers differ. A
  wrong name fails the data read.

## Changing a client

Upstream #428: every in-place UPDATE of a `unifi_client` fails with
`inconsistent result after apply: .last_ip`. Renaming a reservation or moving
its address is therefore a replace:

```bash
terraform apply -replace='module.network.unifi_client.this["hue"]'
```

The client is re-adopted by MAC (`allow_existing`), so the device is untouched;
only the controller-side object is recreated.

## Destroy protection

`unifi_network` and `unifi_firewall_zone` carry `lifecycle.prevent_destroy`.
`lifecycle` blocks take no variables, so this is fixed for every consumer rather
than an input.

- Destroying a **network** drops every client on that VLAN.
- Destroying a **zone** silently returns its networks to the default zone: the
  segmentation is gone, but everything still routes — the failure mode with no
  symptom.

Both are also what a renamed map key plans, which is the point: a rename is a
`moved {}` block. Removing one deliberately is two steps —
`terraform state rm 'module.network.unifi_network.this["<key>"]'` (the live
object is untouched), then delete the entry.

Nothing else is protected, deliberately: a removed **policy** fails closed, and
`unifi_setting` has no delete at all (destroy drops the state entry and changes
nothing on the controller).

## Apply is supervised

This module writes the gateway's own segmentation. A bad apply is not a failed
pipeline, it is a LAN you cannot reach the controller from — so `apply` is a
human-run, plan-reviewed step, and CI runs at most a read-only drift plan.

Back the controller up (`.unf` export) before the first apply of a change that
touches networks or zones.

Two things to read in the first plan specifically:

- **The `unifi_setting.site` `mgmt` and `usg` lines.** "Only the blocks declared
  here are written" is block granularity: those two blocks also carry SSH,
  ui.com remote access, SYN cookies, the ALG modules and every conntrack
  timeout. Undeclared attributes survive because they are Optional+Computed and
  the provider round-trips them, which is a property of the provider rather than
  a promise this module can make. Any of them moving to `null`/`false` in the
  plan is a real change — pin it as an input instead of approving it.
- **The IDS selection.** The module writes `ips_mode` only; the signature
  categories and the inspected networks stay UI-owned. On a console where IDS
  has never been enabled there may be neither, in which case detection-only mode
  inspects nothing — confirm the selection in the console before treating a
  quiet week as evidence for promoting `ids` to `ips`.

## Adopting existing objects

Everything below already exists on a controller that has ever been configured;
import it rather than letting Terraform create a duplicate.

```bash
# The built-in Default network — the only resource with name= import support.
terraform import 'module.network.unifi_network.this["default"]' name=Default

# Everything else with an id: <id>, or <site>:<id>.
terraform import 'module.network.unifi_wlan.this["home"]' 5dc28e5e9106d105bdc87217
terraform import 'module.network.unifi_firewall_zone.this["iot"]' default:5f3e9b2c4ee8cb0f1f4a1234

# Clients import by MAC, and the MAC must contain colons.
terraform import 'module.network.unifi_client.this["hue"]' 00:17:88:7e:c7:a2

# The settings resource's id IS the site name.
terraform import module.network.unifi_setting.site default
```

Ids come from the controller's API (`/proxy/network/v2/api/site/default/...`)
or the object's URL in the UI. Built-in zones are never imported.

## Provider pin

`~> 0.55.0`. This is a pre-1.0 provider and a ground-up rewrite of the
abandoned `paultyng/unifi` one: 0.52 → 0.55 made firewall-policy `index`
read-only, added `unifi_network.purpose`, and changed endpoint match lists to
Computed. Pin the minor here and the exact build in the caller's lockfile; treat
a minor bump as its own change, re-reading the release notes for schema moves.

## Tests

```bash
cd terraform/modules/unifi-network
terraform init -backend=false
terraform test
```

`tests/validation.tftest.hcl` covers every variable validation and every
cross-map precondition — verified by mutation, neutering each one in turn and
confirming a run goes red — plus the derived attributes that have no other
guard: `matching_target` and `port_matching_type` on **both** endpoints, the
ALLOW-only `create_allow_respond`, `dhcp_server.dns_enabled` and the DHCP
start/stop pair, zone `network_ids` membership, `l2_isolation`, the `no2ghz_oui`
inversion, the WPA3/PMF pairing, the conditional `igmp_snooping` block and the
two conditional data reads (the built-in zones, and the client QoS rate a
gateway-only site must skip).
`terraform validate` evaluates no caller values, so it runs none of them; the
runs are plan-only against a `mock_provider`, so they need no controller and
create no state. CI runs the same command through `ci/validate/terraform.yml`
with `test: true`.

A new `validation` block or `precondition` needs a new `run` in the same change:
nothing else in the repo can execute one, and a mock plan is the only place a
derived attribute is observable before it reaches a controller.
