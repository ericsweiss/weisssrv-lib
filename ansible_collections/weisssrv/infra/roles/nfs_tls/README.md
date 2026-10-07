# weisssrv.infra.nfs_tls

Installs and configures **tlshd** (the kernel TLS handshake daemon) so NFSv4 can
run with transport-layer security (`xprtsec=tls` or `xprtsec=mtls`).

## What it deploys

When `nfs_tls_enabled: true`:

- the `ktls-utils` package (`/usr/sbin/tlshd` + its systemd unit)
- `/etc/tlshd.conf` pointing at the host's TLS cert/key and the system CA bundle
- an enabled, running `tlshd.service`

The role only handles the daemon. The handshake itself is triggered by:

- **Server** — an `xprtsec` flag on entries in `/etc/exports`.
  `weisssrv.infra.nas_storage`'s `exports.j2` reads a per-export `xprtsec` field
  on each `nas_storage_exports` item, and a per-client override on each
  `clients[]` entry (the override wins). `tls` **requires** TLS and rejects a
  plaintext mount; `none:tls` is permissive (advertise TLS, still accept
  plaintext); omitting the key leaves the server default (`none:tls:mtls`).

  ```yaml
  nas_storage_exports:
    - path: /tank/media
      xprtsec: "tls"        # require TLS — reject plaintext
      clients:
        - spec: 10.0.0.0/24
          options: rw,sync,no_subtree_check
  ```
- **Client** — `-o xprtsec=tls` in the mount options (`spec.mountOptions` for a
  Kubernetes NFS PV, the storage entry's `options` for Proxmox).

  **A TLS client must mount the server by a hostname the server cert covers.**
  A wildcard cert has no IP SAN, so an IP mount fails the handshake with
  `tlshd: Certificate owner unexpected`.

## Prerequisites

- Kernel ≥ 6.5 and `nfs-utils` ≥ 2.6.3.
- The NFS **server** (`nfs_tls_is_server: true`) needs the cert + key at
  `nfs_tls_cert_path` / `nfs_tls_key_path`, root-owned with the key at **mode
  0600** — the role asserts this and fails loud if the key is group/other
  readable.
- Clients under `xprtsec=tls` need only the **truststore**; they present no
  client certificate and therefore need no private key.

## Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `nfs_tls_enabled` | `false` | Opt-in toggle; set per host or group. |
| `nfs_tls_is_server` | `false` | This host serves NFS over TLS — emit the `[authenticate.server]` block (cert + private key). |
| `nfs_tls_client_cert` | `false` | Client presents a cert; needed only for `xprtsec=mtls`. |
| `nfs_tls_cert_path` | `/etc/ssl/private/fullchain.pem` | Server cert (matches what `weisssrv.infra.acme_certs` distributes). |
| `nfs_tls_key_path` | `/etc/ssl/private/privkey.pem` | Matching private key; asserted mode `0600`. |
| `nfs_tls_truststore` | `/etc/ssl/certs/ca-certificates.crt` | CA bundle for validation. |
| `nfs_tls_scrub_client_cert` | `false` | Set `true` to delete `nfs_tls_cert_path` and `nfs_tls_key_path` on a client-only host. Off by default because another role may own those paths. |

### Private-key least privilege

Under `xprtsec=tls` the authentication is **server-only**: the client validates
the server against the truststore and presents nothing. A client therefore never
needs the server's private key — which, for a wildcard cert, can impersonate
every internal service. The role reflects that: `[authenticate.client]` ships
the key only when `nfs_tls_client_cert: true`, and on a host that is neither a
server nor an mTLS client it can also **remove** a `fullchain.pem`/`privkey.pem`
a previous rollout staged. Keep such hosts out of the cert-distribution target
list entirely; add one back only when migrating an export to `xprtsec=mtls`.

That removal is opt-in (`nfs_tls_scrub_client_cert: true`) and deletes
`nfs_tls_cert_path` and `nfs_tls_key_path`, whose defaults are also where
`weisssrv.infra.acme_certs` installs a cert locally. Leave it off on a host that
legitimately holds a local cert *and* mounts NFS over TLS, or the two roles
delete and reinstall the same files on alternate converges.

## Rollout order

Requiring TLS on an export rejects plaintext, so ordering matters. Stage with
the permissive `none:tls` first if a client cannot be guaranteed ready.

1. Distribute cert + key to the NFS **server** only; verify both files exist.
2. Set `nfs_tls_enabled: true` on the server and every TLS client, then re-run.
   The preflight asserts cert/key on the server (and mTLS clients) and the
   truststore everywhere, so a missing distribution fails loud instead of
   bringing up a misconfigured tlshd.
3. Add `xprtsec` to the relevant export entries (export-level for
   single-audience exports, per-client for mixed ones) and reload `exportfs`.
4. Point the clients at the server **hostname** and add `xprtsec=tls`.
   A Kubernetes PV's `nfs.server` is immutable, so an IP→hostname flip is a
   delete + recreate: delete the PV (with `Retain`, the data is untouched), let
   the GitOps controller recreate it, then restart the consuming workload.
   Proxmox's `server` is create-fixed the same way and needs the storage entry
   removed and re-added.
5. Verify: `xprtsec=tls` in `/proc/mounts` on the clients, successful handshakes
   in `journalctl -u tlshd`, and `exportfs -v` reflecting the per-client value.

### Opting back out

Setting `nfs_tls_enabled: false` and re-running stops and disables `tlshd` and
removes `/etc/tlshd.conf`. The role asks `systemctl list-unit-files` whether the
unit exists, so a host that never installed `ktls-utils` skips the stop cleanly.

## One transport security per client, per server

The NFSv4 client keys its transport and client state **per server IP** and
multiplexes every mount over it, so a node cannot hold a plaintext *and* an
`xprtsec=tls` mount to the same server at once — the second one is refused with
`mount.nfs: Operation not permitted`, whichever order they arrive in. A
successful handshake in the `tlshd` journal is a red herring; the rejection is at
the NFS layer. Cut each node over atomically (recycle all of its NFS-mounting
workloads together, and clear any orphaned plaintext mounts left behind). A
client that only ever mounts plaintext is fine: the rule is that each client is
internally consistent, not that all clients match.

The Kubernetes-side cutover procedure lives in the consumer docs.
