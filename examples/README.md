# examples/

Consumer-side config templates for the scripts in `scripts/`. Nothing here is
read at runtime by the library: copy a file into the consuming repo, edit it, and
point the script at it. Each script's config resolution is documented in
[../docs/SCRIPTS.md](../docs/SCRIPTS.md).

| Example | Script | Conventional destination |
|---|---|---|
| `version-registry.example.py` | `check-versions.py` | `scripts/version-registry.py` |
| `hosts-env-map.example.yml` | `generate-hosts-env.py` | `scripts/hosts-env-map.yml` |
| `deploy-coverage.example.conf` | `check-deploy-coverage.sh` | `scripts/deploy-coverage.conf` |
| `autoscaling-policy.example.yaml` | `check-hpa-vpa-invariant.py`, `validate-helm-values.py` | `kubernetes/autoscaling-policy.yaml` |
| `helm-values-releases.example.yaml` | `validate-helm-values.py` | `helm-values-releases.yaml` |
| `b2-bucket.example.json` | `b2-bucket-drift.py` | `scripts/b2-bucket.json` |
| `netpol-except.example.yaml` | `check-netpol-except-parity.py` | `scripts/netpol-except.yaml` |
| `alertmanager-behaviour.example.yaml` | `check-alertmanager-behaviour.py` | `scripts/alertmanager-behaviour.yaml` |

The values in these files are illustrative placeholders, not a live
configuration. `b2-bucket.example.json` in particular carries `REPLACE-WITH-…`
identifiers; the others name upstreams and layout paths only.
