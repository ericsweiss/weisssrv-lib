# Role: unbound_exporter

Installs the [letsencrypt/unbound_exporter](https://github.com/letsencrypt/unbound_exporter)
on a resolver host to expose Unbound stats (cache hit rate, query counts,
DNSSEC validations) to Prometheus.

Talks to Unbound via `unbound-control` over its local Unix control socket
(`/run/unbound.ctl`, `control-use-cert: no`). The `unbound` role provisions
the socket; the exporter unit runs as a DynamicUser with
`SupplementaryGroups=unbound` for socket access.

## Configuration

```yaml
unbound_exporter_port: 9167
```

`unbound_exporter_version` + `unbound_exporter_checksum` pin the upstream
release `.deb`; upstream publishes no checksum file, so the checksum is ours —
bump the pair together.
