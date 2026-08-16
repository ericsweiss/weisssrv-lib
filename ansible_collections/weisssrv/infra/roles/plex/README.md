# weisssrv.infra.plex

Installs and configures Plex Media Server on a Debian guest — typically an
unprivileged LXC with the library, config and transcode directories bind-mounted
from the host and a GPU passed through for hardware transcoding.

The role manages the guest only. Any reverse proxy / ingress in front of Plex is
somebody else's object.

## What it manages

- the Plex v2 apt repo with a fingerprint-verified signing key (via
  `weisssrv.infra.apt_signed_repo`), the package, and its hold/unhold state
- the non-free apt components and Intel VA-API driver packages for hardware
  transcoding
- the `media` group and the plex user's media / video / render memberships
- a systemd override that fixes the primary-group and umask semantics the
  packaged unit gets wrong for a pooled media tree
- the custom-certificate hook: `/usr/local/sbin/plex-cert-reload.sh` plus the
  PKCS#12 passphrase file

Ordering is the playbook's job — run a base role first, and create the guest
(bind mounts, GPU devices) with `weisssrv.infra.proxmox_lxc` or equivalent.

## Variables

| Variable | Meaning | Required |
|---|---|---|
| `plex_version` | Pinned package version, or `latest` to track the repo | no (`latest`) |
| `plex_user` | Service account created by the package | no (`plex`) |
| `plex_media_group` / `plex_media_gid` | Group owning the media tree; keep in step with `nas_storage_media_group` | no (`media` / `2000`) |
| `plex_config_dir` / `plex_transcode_dir` / `plex_media_dir` | Paths as Plex sees them | no (`/config`, `/transcode`, `/media`) |
| `plex_port` | Plex HTTP/TLS port, used by the readiness check and cert probe | no (`32400`) |
| `plex_claim` | Claim token; rendered into the override only while non-empty | no (`""`) |
| `plex_custom_cert_enabled` | Deploy the PKCS#12 cert hook | no (`true`) |
| `plex_cert_dir` | Where the distributor drops `fullchain.pem` / `privkey.pem` | no (`/etc/ssl/plex`) |
| `plex_pfx_passphrase` | PKCS#12 passphrase (secret); asserted when the hook is on | yes, with the hook |
| `plex_cert_domain` | Fallback SNI for the cert probe | no (`""`) |
| `plex_skip_gpu_drivers` | Skip non-free repos + VA-API drivers | no (`false`) |
| `plex_debian_sources_path` | deb822 sources file whose `Components:` line gets non-free (`/etc/apt/sources.list.d/debian.sources`); a host without it falls back to one-line entries | no |
| `plex_skip_service` | Skip enable/start/readiness **and the bind-mount check** (test containers) | no (`false`) |
| `plex_service_after` | Units the service is ordered after and pulls in | no (`[network-online.target]`) |

## Bind mounts

`plex_config_dir`, `plex_transcode_dir` and `plex_media_dir` must already be
mounted — the role fails loudly rather than creating a local directory that
masks the mount. Existence is not enough: `tasks/assert-mounts.yml` runs
`mountpoint -q` on all three, because a failed or removed mount leaves the
mountpoint directory behind, and Plex would then fill the guest's root
filesystem with a library nothing on the host backs up. `plex_skip_service`
(a container with no real bind mounts) is the only escape from that check. It
deliberately does **not** chown any of the three: they arrive from the host with
UID/GID passthrough, and chowning from inside an unprivileged container rewrites
the host side to high-mapped UIDs, breaking access for everything else. Own them
on the host instead (e.g. `<owner>:media`, mode `2775`).

## Group semantics

The packaged unit forces `Group=plex`. A pooled library on a FUSE mount
(mergerfs with `default_permissions`) honours only the **primary** GID, so Plex's
supplementary `media` membership is ignored there and it cannot write — DVR
recordings fail. The override therefore sets `Group={{ plex_media_group }}`.

Overriding `Group=` replaces the primary group and does not re-add the original,
so `SupplementaryGroups={{ plex_user }}` is mandatory: that group owns the TLS
bundle at `plex_cert_dir` (0640 root:plex), and without it Plex logs
"Found a user-provided certificate, but couldn't install it" and silently serves
its `plex.direct` fallback. `UMask=0002` keeps new DVR files group-writable;
existing library directories need a one-time `chmod -R g+w` **on the host**.

The `video` and `render` groups for GPU transcode still come from the plex
user's own memberships (systemd's initgroups adds them). `render` only exists
where a DRM render node is present, so the role probes for it and skips the
membership when absent.

## Custom certificate

An external distributor (`weisssrv.infra.acme_certs` in this collection) drops
the PEM pair into `plex_cert_dir` and runs `plex-cert-reload.sh`, which converts
it to the PKCS#12 bundle Plex's "Custom certificate location" requires, verifies
the bundle is parseable, swaps it in atomically, restarts Plex, and confirms the
port actually serves the pushed leaf — reverting to `plex.pfx.prev` on any
failure.

Two things an operator must do once, in the Plex UI (Settings -> Network):

- set **Custom certificate encryption key** to the same value as
  `plex_pfx_passphrase`;
- set **Custom certificate location** to `<plex_cert_dir>/plex.pfx` and
  **Custom certificate domain** to the name clients use.

That domain matters beyond the UI: Plex serves the custom certificate **only** to
handshakes whose SNI matches it, and anything else gets the `plex.direct`
fallback. The hook reads the live value out of `Preferences.xml` so changing it
in the UI cannot break the probe; `plex_cert_domain` is only the fallback for a
Plex that has not written the preference yet, and an empty value there makes the
hook refuse to verify blind rather than guess.

## Worked example

```yaml
plex_version: "1.43.3.10861-07dfddaeb"
plex_config_dir: /config
plex_transcode_dir: /transcode
plex_media_dir: /media
plex_media_group: media
plex_media_gid: 2000
plex_cert_domain: vm.example.com
plex_pfx_passphrase: "{{ lookup('ansible.builtin.env', 'PLEX_PFX_PASSPHRASE') }}"
# Only while claiming a fresh server; re-run without it afterwards to drop the
# token from the systemd override.
plex_claim: "{{ lookup('ansible.builtin.env', 'PLEX_CLAIM', default='') }}"
```

With a Proxmox LXC in front, the guest side looks like:

```yaml
proxmox_lxc_bind_mounts:
  - host_path: /mnt/ssd/appdata/plex
    container_path: /config
    options: "mp=/config,backup=1"
  - host_path: /mnt/nvme/fast/plex-transcode
    container_path: /transcode
    options: "mp=/transcode,backup=0"
  - host_path: /mnt/media
    container_path: /media
    options: "mp=/media,ro=0"
# /dev/dri is what the role passes through; there is no device list variable.
proxmox_lxc_gpu_passthrough: true
```

## Testing

```bash
cd roles/plex
molecule -c ../../molecule-shared/base.yml test
```

The scenario installs the real package but skips the GPU drivers and the service
lifecycle, and exercises the cert hook end to end (build, verify, and revert on a
served-certificate mismatch) against a stub `systemctl` and a stub TLS server.
