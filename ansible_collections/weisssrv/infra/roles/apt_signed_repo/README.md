# weisssrv.infra.apt_signed_repo

Shared pipeline to add a **fingerprint-verified signed APT repository** for the
roles that install upstream packages from a vendor apt repo. It ensures the
keyring dir, stats the keyring, optionally installs gnupg, downloads the signing
key, verifies its **primary-key fingerprint** exactly, dearmors it into a binary
keyring, cleans up, then adds the `deb [signed-by=...]` source.

Callers in this collection: **`alloy_host`** (Grafana), **`docker_engine`**
(Docker CE), **`gitlab`** (Omnibus), **`k3s`** (the NVIDIA container toolkit)
and **`plex`** (Plex Media Server). Each keeps its own key URL, fingerprint,
keyring path, repo line and apt filename — the role only owns the mechanics.
`gitlab` is the one caller that uses the `apt_signed_repo_when` seam, to skip
the repo entirely when the install is skipped.

The keyring is the persistent artifact: the download → verify → dearmor →
cleanup sub-sequence is gated on its absence so the role is idempotent. Callers
do their own legacy-keyring cleanup (old `.asc`/`.list` files) before the
include.

`tailscale` uses its own refetch-and-verify flow, which picks up a rotated
upstream key on every run.

## How callers invoke it

```yaml
- name: Install <vendor> signed APT repository
  ansible.builtin.include_role:
    name: weisssrv.infra.apt_signed_repo
  vars:
    apt_signed_repo_key_url: https://vendor.example/gpg.key
    apt_signed_repo_fingerprint: 0123456789ABCDEF0123456789ABCDEF01234567
    apt_signed_repo_keyring_path: /etc/apt/keyrings/vendor.gpg
    apt_signed_repo_repo_line: "deb [signed-by=/etc/apt/keyrings/vendor.gpg] https://vendor.example stable main"
    apt_signed_repo_filename: vendor
```

## Parameters

| Variable | Meaning | Required |
|---|---|---|
| `apt_signed_repo_key_url` | URL of the (ASCII-armored) signing key | yes |
| `apt_signed_repo_fingerprint` | Expected primary-key fingerprint (no spaces) | yes |
| `apt_signed_repo_keyring_path` | Dearmored binary keyring destination | yes |
| `apt_signed_repo_repo_line` | Full `deb [signed-by=...] ...` sources line | yes |
| `apt_signed_repo_filename` | apt `sources.list.d` filename (no extension) | yes |
| `apt_signed_repo_when` | Extra gate threaded through every task (e.g. a skip flag) | no (default `true`) |
| `apt_signed_repo_install_gnupg` | Install gnupg on every run (the existing-keyring re-verify also needs gpg) when nothing else guarantees it | no (default `false`) |
| `apt_signed_repo_keyring_mode` | Keyring mode; empty leaves gpg's umask-dependent output, which apt reports as an unverifiable signature when it cannot read the keyring | no (default `"0644"`) |
| `apt_signed_repo_stage_dir` | Root-only directory the key is staged in | no (default `/run/apt-signed-repo`) |
| `apt_signed_repo_tmp_key` | Staging path for the download | no (default `<stage_dir>/<keyring-basename>.download`) |
| `apt_signed_repo_update_cache` | Refresh the apt cache when the repo is added; set `false` for hermetic tests/staged rollouts | no (default `true`) |

## Other entry points

| `tasks_from` | What it does | Inputs |
|---|---|---|
| `enable-components.yml` | Rewrites every `Components:` line in a deb822 sources file to one canonical set | `apt_signed_repo_sources_path` (default `/etc/apt/sources.list.d/debian.sources`), `apt_signed_repo_components` (default `main contrib non-free non-free-firmware`), `apt_signed_repo_update_cache` (default `true`) |

```yaml
- name: Enable contrib/non-free/non-free-firmware components
  ansible.builtin.include_role:
    name: weisssrv.infra.apt_signed_repo
    tasks_from: enable-components.yml
  vars:
    apt_signed_repo_sources_path: "{{ k3s_gpu_debian_sources_path }}"
    apt_signed_repo_components: main contrib non-free non-free-firmware
```

`enable-components.yml`:

- fails on an empty `apt_signed_repo_components`, a missing sources file, or a
  file with no `Components:` line. Each would otherwise surface later as an
  opaque "Unable to locate package" in the calling role;
- is idempotent from any partial state, and check-mode safe: it reads the file
  before the rewrite, under `--check` or a caller's `check_mode: true`;
- refreshes the apt cache on a real rewrite unless
  `apt_signed_repo_update_cache` is false;
- handles deb822 only. A host on the one-line sources format needs
  `apt_repository`.

## Key rotation

Rotation is delete-the-keyring-first. An installed keyring whose primary
fingerprint does not match `apt_signed_repo_fingerprint` fails the run on every
host. That is the fail-closed tamper check: the role never replaces a keyring it
did not expect, so a swapped key is reported rather than overwritten.

When a vendor genuinely rotates its signing key, the play is:

1. Confirm the new fingerprint from the vendor's own published source.
2. Remove `apt_signed_repo_keyring_path` on the affected hosts.
3. Bump `apt_signed_repo_key_url` and `apt_signed_repo_fingerprint`, and re-run.

Caller roles link here rather than restating the play.

## Staging path

Fingerprint verification and dearmor are separate SSH invocations against the
downloaded file, so anything that can rewrite that file in between chooses which
key apt trusts. The role therefore stages into a root-owned `0700` directory
under a non-world-writable parent, and removes it after dearmor. Override
`apt_signed_repo_stage_dir` only with a path holding the same property —
`/tmp/...` does not.

## See also

- `weisssrv.infra.prometheus_exporter` — the analogous shared-pipeline role
