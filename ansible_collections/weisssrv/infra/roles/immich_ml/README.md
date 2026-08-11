# weisssrv.infra.immich_ml

Deploys the Immich machine-learning service (`immich-machine-learning`,
**OpenVINO** variant) as a single-service Docker Compose stack in a GPU guest —
typically a container that shares the host's `/dev/dri` rather than a VM, since
VFIO passthrough is exclusive and the card usually serves other consumers too.
An Immich guest consumes this as its **primary** ML endpoint (`immich_ml_urls`),
keeping its own CPU ML container as the failover.

## Design

- **GPU share, not exclusive passthrough**: the host's `/dev/dri` is bind-mounted
  into the guest (`weisssrv.infra.proxmox_lxc`, `lxc_gpu_passthrough`), so the
  kernel driver arbitrates between consumers. The `-openvino` image bundles its
  own Intel compute-runtime; the host supplies only the kernel driver.
- **Version lockstep**: `immich_ml_version` defaults to the inventory-wide
  `immich_version` the Immich guest uses, so one pin bump redeploys both sides
  with matching tags.
- **Failover**: `immich-server` tries `machineLearning.urls` in order, so an
  outage here degrades to the Immich guest's CPU ML rather than to broken ML.
- **Authless endpoint**: the ML API has no authentication by upstream design. A
  firewall rule admitting **only** the Immich guest on `immich_ml_listen_port`
  IS the security boundary.
- **No state**: the multi-GB model cache is a named docker volume in the guest's
  root filesystem — re-downloadable cache, not data. No attached volumes, no
  backup enrollment, so the guest can boot unattended.

## What it does

1. **GPU guard** — asserts the render + card device nodes exist in the guest and
   reads their GIDs for the container's `group_add` (in an unprivileged guest
   the group match is the only way to open the 0660 device nodes).
2. **Docker Engine** (`compose_app` → `docker_engine`) — pinned, `dpkg`-held
   engine + compose plugin, journald log driver.
3. **Compose stack** — mirrors upstream's `hwaccel.ml.yml` openvino stanza
   (device dir, `c 189:*` cgroup rule, `model-cache` volume, port 3003).
   Lifecycle: `immich-ml-compose.service` (no `RequiresMountsFor`, no volumes).
4. **Health wait** — polls `/ping`, which answers before the first-boot model
   download finishes (models load lazily).

## Parameters

| Variable | Meaning | Default |
|---|---|---|
| `immich_ml_version` | Immich release tag; defaults to the inventory-wide `immich_version` | **required** |
| `immich_ml_image` | Full image ref; override whole for a non-Intel variant | `…/immich-machine-learning:<version>-openvino` |
| `immich_ml_skip_install` | Render-only: skip the GPU guard, Docker install and service management (alias: `skip_immich_ml_deploy`) | `false` |
| `immich_ml_compose_dir` | Compose project dir | `/opt/immich-ml/compose` |
| `immich_ml_listen_port` | Published port (container listens on 3003) | `3003` |
| `immich_ml_timezone` | Container `TZ` | `timezone` or `UTC` |
| `immich_ml_render_device` / `_card_device` | Device nodes asserted present; their GIDs feed `group_add` | `/dev/dri/renderD128`, `/dev/dri/card0` |
| `immich_ml_device_dir` | Device dir handed to the container | `/dev/dri` |
| `immich_ml_video_gid` / `_render_gid` | `group_add` fallbacks, overwritten by the discovered GIDs | `44` / `104` |
| `immich_ml_health_retries` / `_health_delay` | `/ping` wait budget | `30` × `10s` |

## Molecule

`molecule/default` is a render/contract scenario: `immich_ml_skip_install: true`
skips the GPU guard, the Docker install and service management, so the compose
file (the `-openvino` tag derived from the release pin, the device mapping,
port, timezone and `group_add` GIDs) and the systemd unit are rendered and
asserted without a container runtime or a GPU.

## Related

- `weisssrv.infra.proxmox_lxc` — creates the guest with the GPU passthrough and
  the video/render GID idmap.
- `weisssrv.infra.compose_app` — Docker install + compose systemd unit.
- `weisssrv.infra.immich` — renders `machineLearning.urls` (this endpoint first,
  its CPU container second).
- `weisssrv.infra.alloy_host` — journald → Loki log shipping.
