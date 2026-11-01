# weisssrv.infra.docker_engine

Shared **pinned Docker Engine (CE)** install pipeline for docker-compose app
guests: install the apt prerequisites, add the fingerprint-verified
`download.docker.com` apt repo (via `apt_signed_repo`), install the exact-pinned
engine + CLI + containerd + buildx + compose plugins, `dpkg`-**hold** them so a
routine `apt upgrade` cannot bump the engine out from under a running stack, and
write the journald `/etc/docker/daemon.json`.

Holding is the point of the role: an install flow that pins but does not hold
lets the next `apt upgrade` move the engine anyway.

## Versions

The four version pins are **required inputs** — there is no default, because a
stale default would silently downgrade an engine. The role asserts all four are
non-empty (unless `docker_engine_skip_install`). Supply them from wherever the
site keeps its component pins:

```yaml
docker_engine_ce_version: "5:28.5.2-1~debian.13~trixie"
docker_engine_containerd_version: "1.7.29-1"
docker_engine_buildx_plugin_version: "0.30.0-1~debian.13~trixie"
docker_engine_compose_plugin_version: "2.41.1-1~debian.13~trixie"
```

Bumping a pin reconciles through the hold (`allow_change_held_packages`), and a
host that drifted **above** the pin converges back down (`allow_downgrade`).

## Parameters

| Variable | Meaning | Default |
|---|---|---|
| `docker_engine_ce_version` | Engine + CLI apt version | **required** |
| `docker_engine_containerd_version` | containerd.io apt version | **required** |
| `docker_engine_buildx_plugin_version` | buildx plugin apt version | **required** |
| `docker_engine_compose_plugin_version` | compose plugin apt version | **required** |
| `docker_engine_skip_install` | Skip every step needing the real apt repo / Docker binary / running daemon (render-only); the `/etc/docker` dir + `daemon.json` still render | `false` |

## Key rotation

The signing key is fail-closed: a keyring whose fingerprint stops matching the
pin fails the run rather than being replaced. Rotate by deleting
`/etc/apt/keyrings/docker.gpg` on the affected hosts, then bumping the key URL and
fingerprint in `tasks/main.yml`. See `apt_signed_repo`'s README § Key rotation.

## See also

- `weisssrv.infra.apt_signed_repo` — the signed-repo helper this role includes
- `weisssrv.infra.compose_app` — the compose-guest role that includes this one
