# weisssrv-new-project

The scaffolding CLI for weisssrv cluster tenant repos. It turns a fresh copy of
[weisssrv-project-template](https://git.ericsweiss.com/eric/weisssrv-project-template)
into a configured project. Offline and dependency-light — the only runtime
dependency is `ruamel.yaml`, which handles both the round-trip edits that
preserve the scaffold's comments and the plain safe-loads used to inspect
documents. No PyYAML is needed at runtime (it is a test-only extra).

## Install / run

```bash
# From a checkout of this library:
pip install ./cli            # installs the `weisssrv-new-project` console script
# or run without installing:
PYTHONPATH=cli python3 -m weisssrv_lib_cli --help
```

## Commands

Run each from the project root, or pass `--root <dir>`.

### rename `<app-slug> <gitlab-group>`

Replaces the `changeme-app` / `changeme-group` placeholders across the tracked
tree. The slug must be a DNS label (it becomes the namespace and Flux
Kustomization name); the group is a GitLab namespace path (may be nested).

```bash
weisssrv-new-project rename recipe-box eric/apps
```

### prune `<feature...>`

Structurally removes components you don't need (deletes the manifest, drops its
kustomization entry, and cleans cross-references):

| feature            | effect |
|--------------------|--------|
| `secrets`          | delete externalsecret.yaml + the deployment secret env block |
| `metrics`          | delete servicemonitor.yaml + the observability-scrape NetworkPolicy |
| `pdb`              | delete pdb.yaml |
| `single-replica`   | delete pdb.yaml + set the deployment to `replicas: 1` |
| `hpa`              | delete the opt-in hpa.yaml |
| `external-ingress` | remove the public IngressRoute + Certificate (keep the internal variants) |
| `image-build`      | delete a repo-root Dockerfile / .dockerignore |
| `manifest:<file>`  | delete kubernetes/flux/&lt;file&gt;.yaml + its kustomization entry |

```bash
weisssrv-new-project prune metrics single-replica
# internal-only: wire the internal route first, then drop the public one
weisssrv-new-project wire internal-ingress && weisssrv-new-project prune external-ingress
```

### wire `<feature...>`

Enables opt-in components (uncomments the shipped-commented blocks and makes the
paired data edits):

| feature            | effect |
|--------------------|--------|
| `hpa`              | add hpa.yaml, uncomment the HPA, drop `replicas`, make the VPA memory-only |
| `internal-ingress` | activate the internal IngressRoute + Certificate (`*.esweiss.com`) |
| `sso`              | add the Authentik forward-auth middleware to the public route |

```bash
weisssrv-new-project wire hpa
```

### verify

Sanity-checks the result: no placeholder tokens remain, every kustomization
resource exists on disk, no manifest is orphaned, and (when `kustomize` is on
PATH) `kustomize build kubernetes/flux` succeeds. Exit non-zero on any hard
problem.

```bash
weisssrv-new-project verify          # runs kustomize build if available
weisssrv-new-project verify --no-kustomize
```

## Tests

```bash
python3 -m pytest cli/tests -q
```

The suite copies the bundled scaffold fixture into a tmpdir per test and
exercises every command (including a full rename → prune → wire → verify flow),
mirroring the weisssrv `scripts:test` throwaway-tree pattern. No network, no
install required.
