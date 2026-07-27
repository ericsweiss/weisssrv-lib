# Role: smtp_relay

Postfix as a central relay: it accepts authenticated submissions from the
estate's null clients (`weisssrv.infra.postfix_null_client`) and forwards them
to an upstream smarthost over SASL + TLS. Also ships a queue-depth textfile
collector so a wedged upstream is alertable.

## Variables

| Variable | Default | Meaning |
|---|---|---|
| `smtp_relay_hostname` | `smtp-relay.{{ internal_domain }}` | `myhostname` in the default config |
| `smtp_relay_origin` | `{{ internal_domain }}` | `myorigin` in the default config |
| `smtp_relay_upstream` | `""` | `relayhost`, e.g. `[smtp.example.net]:587`. Empty = direct delivery |
| `smtp_relay_config` | see `defaults/main.yml` | the whole `main.cf` map, rendered verbatim |
| `smtp_relay_tls_cert_dir` | `/etc/postfix/tls` | where `fullchain.pem` + `privkey.pem` are expected |
| `smtp_relay_submission_enabled` | `true` | the 587 service in `master.cf` |
| `smtp_relay_submission_config` | see `defaults/main.yml` | per-service `-o` overrides for 587 |
| `smtp_relay_aliases` | root → `admin_email` | `/etc/aliases` map |
| `smtp_relay_textfile_dir` | tracks `node_exporter_host_textfile_dir` | where the queue collector writes its `.prom` |

Required credentials, no defaults:

| Variable | Used for |
|---|---|
| `smtp_relay_upstream_user` / `smtp_relay_upstream_password` | outbound SASL to the smarthost (`/etc/postfix/sasl_passwd`) |
| `smtp_relay_sasl_user` / `smtp_relay_sasl_password` | inbound null-client auth, held in the local sasldb |

Every Postfix boolean in `smtp_relay_config` is the **string** `"yes"`/`"no"`,
never a YAML boolean — the templates render values verbatim.

## Security posture the defaults encode

- Port 25 accepts **no** AUTH (`smtpd_sasl_auth_enable: "no"`): it is chrooted,
  which breaks the saslauthd socket anyway, and enabling it there would permit
  cleartext AUTH. Submission (587) re-enables AUTH per-service behind
  `smtpd_tls_security_level: encrypt`.
- `smtpd_relay_restrictions` drops `permit_mynetworks` by default, so only
  SASL-authenticated senders relay. A deployment that wants unauthenticated
  relay from a host LAN opts in explicitly by overriding `mynetworks` **and**
  `smtpd_relay_restrictions`.
- Outbound TLS is `secure`, not `encrypt`: the smarthost's certificate is
  verified against `smtp_tls_CAfile`. `encrypt` alone accepts any certificate.
- Secrets reach `saslpasswd2` through the environment, never argv, and every
  task that touches one carries `no_log` — including the sasldb probe, whose
  grep pattern embeds the relay username.

## Credential rotation

sasldb stores nothing comparable in plaintext, so rotation is detected with a
fingerprint: `sha256(user@host:password)` is written to
`/etc/postfix/.sasl_relay_user.sha256` **after** `saslpasswd2` succeeds. A
changed credential changes the fingerprint, which makes the always-run
`saslpasswd2` report changed and reload Postfix; a failed rotation leaves the
old fingerprint so the next run retries.

## Monitoring

`postfix-queue-collector.sh` runs on a timer via
`weisssrv.infra.textfile_collector` and emits `postfix_queue_depth` /
`postfix_up`. A wedged upstream (expired credential, rate limit) leaves Postfix
`active` while the deferred queue grows, so queue depth — not service state — is
the signal worth alerting on.
