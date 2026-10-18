# Database connection contract

How an application consumes a CloudNativePG (CNPG) database without knowing
about the operator. Contract version 1. Renaming a Secret or a key, or changing
a key's meaning, is a MAJOR change to this library and to every template that
renders it.

## Secrets

For an application `<app>` with CNPG cluster `<app>-db`, database `<db>` and
role prefix `<prefix>` (a lowercase PostgreSQL identifier, separate from `<app>`
because `<app>` may contain hyphens), three Secrets exist in the application's
namespace, one per login role:

| Secret | Role | Use |
|---|---|---|
| `<app>-db-migrate` | `<prefix>_migrate`, owner of `<db>` | schema migrations only |
| `<app>-db-app` | `<prefix>_app` | the application at runtime |
| `<app>-db-ro` | `<prefix>_ro` | reporting, read-only integrations, the logical dump |

Each is `type: kubernetes.io/basic-auth`, labelled `cnpg.io/reload: "true"` so
the operator re-applies a rotated password, and carries exactly these keys:

| Key | Value |
|---|---|
| `username`, `user` | the role name (CNPG reads `username`; libpq clients read `user`) |
| `password` | the role's password |
| `host` | `<app>-db-rw`, the read-write Service; never a pooler |
| `port` | `5432` |
| `dbname` | `<db>` |
| `sslmode` | `verify-full` |
| `uri` | `postgresql://<role>:<urlencoded password>@<app>-db-rw:5432/<db>?sslmode=verify-full` |

The server CA is the CNPG-generated Secret `<app>-db-ca`, key `ca.crt`. Clients
mount that one key (see Delivery) and pass it as `sslrootcert`; the server
certificate names the `<app>-db-rw` Service, so `verify-full` holds.

## Delivery

Secrets reach pods as files: mount the Secret as a volume (never with
`subPath`, which blocks in-place rotation) and read each key from its file.
Each workload mounts only the role Secret it connects as, whole: migrations (a
Job or an init container) `<app>-db-migrate`, runtime containers `<app>-db-app`,
reporting or logical-dump Jobs `<app>-db-ro`. No container mounts a role Secret
it does not use. The CA Secret `<app>-db-ca` also
carries `ca.key`, the CA's signing key, which must never reach an application
pod, so its volume projects only `ca.crt` through `items`. An `items`
projection still updates in place on rotation; `subPath` does not.

```yaml
volumes:
  - name: db-ca
    secret:
      secretName: <app>-db-ca
      items:
        - key: ca.crt
          path: ca.crt
```
Passwords come from one secrets-backend item with fields `migrate-password`,
`app-password` and `ro-password`, templated into the three Secrets by an
ExternalSecret each; the owner Secret is also `bootstrap.initdb.secret`, and the
other two are `spec.managed.roles[].passwordSecret`. `bootstrap.initdb.secret`
is not read only at initdb: the primary's instance manager re-applies the
owner's password whenever that Secret changes, as it does the superuser's
(CloudNativePG 1.30, Labels and annotations, `cnpg.io/passwordPassthrough`,
which lists the application user's Secret among those the operator consumes).

## Privileges

`<prefix>_app` and `<prefix>_ro` are created `NOLOGIN` at initdb so default
privileges can name them, then given `LOGIN` by `spec.managed.roles`. For
objects `<prefix>_migrate` creates in schema `public`, `<prefix>_ro` gets
`SELECT` on tables and sequences, and `<prefix>_app` gets `SELECT, INSERT` on
tables and `USAGE, SELECT` on sequences. `UPDATE` and `DELETE` exist only where
a migration grants them, so append-only tables stay append-only by default.

## Rotation

Change the field in the secrets backend, then force the ExternalSecret
(`kubectl annotate externalsecret <name> force-sync=$(date +%s) --overwrite`).
The operator runs `ALTER ROLE` from the reloaded Secret; file mounts update in
place, so a client that reads the file at connect time picks up the new password.
A running process or connection pool still holds the old password, so a
long-running consumer re-reads the password file before each new connection or
is reloaded after CNPG has reconciled the role.

## Gates

`scripts/check-cnpg-clusters.py` holds each `Cluster` to its backup, retention,
drain and prune safeguards, and `scripts/check-pvc-storageclass.py` to a pinned
storage class.
