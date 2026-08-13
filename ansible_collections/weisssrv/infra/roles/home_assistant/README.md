# weisssrv.infra.home_assistant

Deploys `configuration.yaml` + `secrets.yaml` to a **Home Assistant OS**
appliance over the HAOS SSH add-on.

HAOS is not a manageable Linux host — there is no Python, no sudo, no package
manager to drive. So the role renders both files on the play host (normally
`localhost`), pushes them, validates them with `ha core check`, and rolls back
if the check fails. It manages nothing else on HAOS.

## What it does

1. Asserts its inputs, then pins the HAOS SSH host key in the deploying user's
   `known_hosts` (every `ssh`/`scp` runs with `StrictHostKeyChecking=yes`).
2. Pre-flights the TLS cert files when `home_assistant_ssl_enabled`.
3. Renders both files into a private staging directory.
4. Compares a **sha256 of each rendered file with the deployed one** over one
   ssh round trip. Identical → the whole stage → backup → install → check chain
   is skipped, so a converged HAOS reports no change and `ha core check` does
   not run.
5. Otherwise: scp both files to HAOS `/tmp`, back up the current pair to
   `*.yaml.bak`, `mv` them into place (secrets first, so `!secret` references
   always resolve), run `ha core check`, then delete the backups.
6. On failure, the rescue restores the pre-deploy state: a `.bak` is restored,
   and a file this deploy created is removed. The staging directory and any
   HAOS `/tmp` leftovers are cleaned up in `always`.

A config change takes effect on the next Home Assistant restart; the role does
not restart it.

## Variables

| Variable | Meaning | Required |
| --- | --- | --- |
| `home_assistant_host` | Address the HAOS SSH add-on listens on | yes (deploy) |
| `home_assistant_host_key` | Pinned SSH host key — algorithm + base64, no host prefix | yes (deploy) |
| `home_assistant_trusted_proxies` | CIDRs allowed to set `X-Forwarded-For` | yes |
| `home_assistant_oidc_configure_url` | Identity provider's OIDC discovery URL | yes |
| `home_assistant_oidc_client_id` / `_client_secret` | OIDC client credentials | yes (env defaults) |
| `home_assistant_oidc_scope` | OIDC scopes | no (`openid profile email`) |
| `home_assistant_oidc_username_field` | Claim mapped to the HA username | no (`preferred_username`) |
| `home_assistant_oidc_block_login` | Disable HA's own password login | no (`true`) |
| `home_assistant_ssl_enabled` | Terminate TLS on HAOS itself | no (`false`) |
| `home_assistant_ssl_certificate` / `_ssl_key` | Cert paths on HAOS | no (`/ssl/fullchain.pem`, `/ssl/privkey.pem`) |
| `home_assistant_ssh_user` / `_ssh_port` | HAOS SSH login | no (`root`, `22222`) |
| `home_assistant_ssh_connect_timeout` | `ConnectTimeout` for every call | no (`10`) |
| `home_assistant_config_path` | Config dir on HAOS | no (`/config`) |
| `home_assistant_extra_config` | Extra YAML appended verbatim to `configuration.yaml` | no (`""`) |
| `home_assistant_enable_prometheus` / `_enable_default_config` | Emit the `prometheus:` / `default_config:` block | no (`true`) |
| `home_assistant_tts_platforms` | TTS platforms; empty omits the `tts:` block | no (`[google_translate]`) |
| `home_assistant_includes` | `<key>: !include <file>` map; empty omits them | no (`automation`/`script`/`scene`) |
| `home_assistant_staging_dir` | Pin the staging dir; empty uses a private per-run tempdir | no (`""`) |
| `home_assistant_render_only` | Render and stop — no ssh/scp/check | no (`false`) |

`home_assistant_oidc_client_id` / `_client_secret` default to the
`HA_OIDC_CLIENT_ID` / `HA_OIDC_CLIENT_SECRET` environment variables, which is
how a secret manager (`op run -- ansible-playbook ...`) supplies them without
putting them in inventory. Set the variables directly to bypass that.

`home_assistant_ssl_enabled` is `false` by default on purpose: HA fails to bind
8123 when `configuration.yaml` names a cert file that is not on the appliance
yet. Turn it on once the cert has been distributed to `/ssl`.

### Worked example

```yaml
home_assistant_host: 192.168.0.154
home_assistant_host_key: "ssh-ed25519 AAAAC3Nza...example..."
home_assistant_trusted_proxies:
  - 192.168.0.0/24     # LAN — the ingress node handling traffic can change
  - 10.42.0.0/16       # k3s pod network
  - 10.43.0.0/16       # k3s service network
home_assistant_oidc_configure_url: >-
  https://auth.example.com/application/o/home/.well-known/openid-configuration
home_assistant_ssl_enabled: true
```

The trusted-proxy list is deliberately broad in that example: the reverse proxy
runs on cluster nodes with LAN addresses and the node handling a request is not
fixed, so the list covers the LAN plus the cluster's pod/service CIDRs.

## Host-key pinning (required)

HAOS SSH is `root` with no `sudo`, and this channel carries secrets. The usual
`accept-new` TOFU path is therefore not acceptable: a re-imaged, factory-reset
or restored HAOS would register a new key and still receive the push. Pin the
key instead — the role asserts it is set, writes it to `known_hosts`, and runs
every call with `StrictHostKeyChecking=yes`.

Capture it once:

```bash
ssh-keyscan -t ed25519 -p 22222 192.168.0.154
```

Set the algorithm + base64 portion (everything **after** `[host]:port`) as
`home_assistant_host_key` in inventory.

After a HAOS rebuild the pre-flight fails with a host-key mismatch. Recover by
dropping the stale local entry, re-capturing, and updating the pin:

```bash
ssh-keygen -R "[192.168.0.154]:22222"
ssh-keyscan -t ed25519 -p 22222 192.168.0.154
```

Committing the new pin is the point: a HAOS re-key is a real event that should
be reviewable.

## Configuration surface

`configuration.yaml.j2` renders the `http:` block (X-Forwarded-For + trusted
proxies, optional TLS), the `openid:` SSO block, `prometheus:`,
`default_config:`, `tts:` and the `automation`/`script`/`scene` includes. The
last four are opinionated defaults, not fixtures: `home_assistant_enable_
prometheus`, `_enable_default_config`, `_tts_platforms` and `_includes` each
omit their block when disabled or emptied, which matters because
`home_assistant_extra_config` can only append and `ha core check` fails the
deploy when an `!include` target does not exist on the host.
Anything else belongs in `home_assistant_extra_config` or in HA's own UI
storage. SMTP notifications in particular are no longer YAML: the `smtp` notify
platform was removed in HA 2027.1.0 and lives in a UI config entry (kept in
HAOS `.storage`, captured by HA backups).

`secrets.yaml.j2` renders `oidc_client_id` / `oidc_client_secret`, each passed
through `to_json` so a value containing `:` or `[` stays valid YAML.

Both templates must stay **byte-stable** across runs — the idempotency check is
a checksum comparison, so a timestamp in the render would defeat it.

## Prerequisites on HAOS

The SSH add-on must be installed and listening (port 22222 by convention) with
the deploying user's public key authorized. Integrations, automations and
backups are managed in HA's UI, not here.

## Layout

```
roles/home_assistant/
├── defaults/main.yml       consumer API + documented defaults
├── vars/main.yml           internal shared ssh option set
├── meta/main.yml           galaxy metadata
├── tasks/main.yml          assert -> render -> compare -> deploy/rescue
├── templates/
│   ├── configuration.yaml.j2
│   └── secrets.yaml.j2
├── molecule/default/       render contract + deploy/rollback via local shims
└── README.md
```

## Testing

The molecule scenario converges with `home_assistant_render_only: true` and
asserts on the rendered files, then drives the stateful path with local
`ssh`/`scp`/`ha` shims against a fake `/config`: successful install, an
unchanged re-run that must skip the install chain, a rollback on a failed
`ha core check`, and cleanup of a failed first deploy.
