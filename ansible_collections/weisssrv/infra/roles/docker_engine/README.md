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

## How callers invoke it

```yaml
- name: Install Docker Engine + compose plugin
  ansible.builtin.include_role:
    name: weisssrv.infra.docker_engine
  when: not (<role>_skip_install | default(false))
```

The `daemon.json` task notifies this role's own `Restart docker` handler, so the
caller does not need to carry one.

## Parameters

| Variable | Meaning | Default |
|---|---|---|
| `docker_engine_ce_version` | Engine + CLI apt version | **required** |
| `docker_engine_containerd_version` | containerd.io apt version | **required** |
| `docker_engine_buildx_plugin_version` | buildx plugin apt version | **required** |
| `docker_engine_compose_plugin_version` | compose plugin apt version | **required** |
| `docker_engine_skip_install` | Skip every step needing the real apt repo / Docker binary / running daemon (render-only); the `/etc/docker` dir + `daemon.json` still render | `false` |
| `docker_engine_key_url` | download.docker.com signing key URL | download.docker.com/linux/debian/gpg |
| `docker_engine_key_fingerprint` | Expected primary-key fingerprint | Docker's `9DC8…CD88` |
| `docker_engine_repo_url` | apt repo base URL | download.docker.com/linux/debian |
| `docker_engine_keyring_path` | Dearmored keyring destination | `/etc/apt/keyrings/docker.gpg` |
| `docker_engine_packages` | Pinned `name=version` list to install | engine + CLI + containerd + buildx + compose, from the four version inputs |
| `docker_engine_hold_packages` | Package names to `dpkg`-hold | same five, unversioned |
| `docker_engine_daemon_config` | dict rendered to `/etc/docker/daemon.json` | journald log driver + live-restore |

## Molecule

`molecule/default` runs render-only (`docker_engine_skip_install: true`): it
asserts the journald `daemon.json` and, from a seeded pre-standardization repo
line + keyring, that the legacy-keyring cleanup removes both.

## See also

- `weisssrv.infra.apt_signed_repo` — the signed-repo helper this role includes
- `weisssrv.infra.compose_app` — the compose-guest role that includes this one
