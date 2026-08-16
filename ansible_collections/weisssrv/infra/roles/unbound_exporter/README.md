# Role: unbound_exporter

Installs the [letsencrypt/unbound_exporter](https://github.com/letsencrypt/unbound_exporter)
on a resolver host so Prometheus can scrape Unbound stats (cache hit rate, query
counts, DNSSEC validations).

Thin wrapper over `weisssrv.infra.prometheus_exporter` (`.deb` artifact): that
role downloads, checksum-verifies, installs, enables, and health-checks; this
role only supplies the pins and its own unit file.

The exporter talks to Unbound through the local Unix control socket
`/run/unbound.ctl` (`control-use-cert: no`), provisioned by
`weisssrv.infra.unbound`. The unit runs as a `DynamicUser` with
`SupplementaryGroups=unbound` for socket access, so no static account is created.

## Inputs

| Variable | Default | Purpose |
|---|---|---|
| `unbound_exporter_port` | `9167` | `--web.listen-address` port and the health-check target |
| `unbound_exporter_version` | `0.6.0` | Upstream release tag; also what the dpkg version check compares against |
| `unbound_exporter_checksum` | `sha256:4f61876a…` | Upstream publishes no checksum file, so this is our own sha256 of the release `.deb` — bump it together with the version |

**x86_64 only.** The download URL names the upstream `.x86_64.deb` and
`unbound_exporter_checksum` pins that one artefact, so the role asserts
`ansible_architecture == 'x86_64'` at entry; another architecture needs a
per-arch artefact *and* a per-arch checksum.

The installed version is read with `dpkg-query`, not from the binary: it has no
`--version` flag. The Debian version is normalized (epoch and revision stripped)
before comparison with the pin.

## See also

- `weisssrv.infra.unbound` — the resolver and its control socket
- `weisssrv.infra.prometheus_exporter` — the shared install/enable pipeline
