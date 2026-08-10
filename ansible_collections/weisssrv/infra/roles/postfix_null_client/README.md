# weisssrv.infra.postfix_null_client

Configures Postfix as a **null client** (satellite): no local delivery, every
message — cron output, alerts, `mail root` — is relayed over SASL-authenticated,
certificate-verified STARTTLS to a central relay.

## What it manages

- `main.cf` rendered from `postfix_null_client_config` (loopback-only listener,
  `mydestination` limited to `$myhostname`)
- `/etc/mailname`
- `sasl_passwd` (+ `postmap`, mode `0600`) and `/etc/aliases`
- optional `virtual` alias table, removed again when the variable goes away
- repair of the **compiled** maps (`tasks/repair-compiled-maps.yml`, run before
  postfix starts): both sources are templated with `notify:`, so a run that dies
  before `flush_handlers` leaves a correct source next to a stale `.db` — and
  postfix reads the `.db`. The checks compare the compiled value against the
  configured one, because a `.db` serving a retired alias target or a revoked
  credential still satisfies a "resolves to something" test. `smtp_relay`
  includes the same file (`tasks_from: repair-compiled-maps.yml`) with
  `postfix_null_client_maps_root_alias` set to its own root target.

## Variables

| Variable | Meaning | Required |
|---|---|---|
| `postfix_null_client_relay_host` | Relay hostname. Must match a SAN on the cert the relay presents — `smtp_tls_security_level: secure` verifies it | yes |
| `postfix_null_client_mail_domain` | Domain appended to `inventory_hostname` for `myhostname` / `/etc/mailname` | yes |
| `postfix_null_client_sasl_user` | SASL user for the relay | yes |
| `postfix_null_client_sasl_password` | SASL password for the relay | yes |
| `postfix_null_client_relay_port` | Relay port | no (`587`) |
| `postfix_null_client_root_alias` | Where root's mail is forwarded | no (`root@localhost`) |
| `postfix_null_client_config` | Full `main.cf` key/value map | no (rendered from the above) |
| `postfix_null_client_aliases` | `/etc/aliases` map | no (postmaster/nobody/hostmaster/webmaster → root, root → `_root_alias`) |
| `postfix_null_client_virtual_aliases` | List of `{from, to}` entries for `virtual` | no (undefined = no table) |

Both credentials are handled with `no_log`. Supply them from the site's secret
store; never commit them.

```yaml
- hosts: all
  roles:
    - role: weisssrv.infra.postfix_null_client
      vars:
        postfix_null_client_relay_host: smtp-relay.example.com
        postfix_null_client_mail_domain: example.com
        postfix_null_client_root_alias: ops@example.com
```

## Checking a converged host

```bash
echo "Test from $(hostname)" | mail -s "Test Subject" root
mailq
tail -f /var/log/mail.log
```
