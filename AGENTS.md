# AGENTS.md

Guidance for agents working in **weisssrv-lib** — the shared CI/tooling library
and Ansible collection that the weisssrv family pins.

This file is a pointer, not a second copy. Anything stated in `README.md`,
`docs/INCLUDE-CONTRACT.md` or `docs/VERSIONING.md` is canonical there; what
follows is the working discipline and the traps that are not obvious from the
files themselves. [CLAUDE.md](CLAUDE.md) points here too — it is not a rival
source.

## What this repo is

One source of truth for everything the family shares:

- **`ci/`** — GitLab CI templates with `spec:inputs`, including the Ansible
  deploy toolchain in `ci/deploy/` (adoption per template is tracked in
  [docs/INCLUDE-CONTRACT.md](docs/INCLUDE-CONTRACT.md)), plus the
  `*.example.yml` GitHub workflows, which are the one thing here a
  forge-portable consumer VENDORS rather than includes.
- **`ansible_collections/weisssrv/infra/`** — every host-configuration role,
  consumed by FQCN (`weisssrv.infra.<role>`), versioned by the same tag.
- **`terraform/modules/`** — four module shapes (cloudflare-zone,
  tailscale-acl, authentik-sso, unifi-network).
- **`scripts/`** — the gates and generators the CI jobs run.
- **`kubernetes/reapers/`** — the shipped reaper programs a consumer vendors
  into its own cluster (see [docs/SCRIPTS.md](docs/SCRIPTS.md)).
- **`lint/`** — linter configs a consumer vendors.
- **`cli/`** — `weisssrv-new-project`, the copier wrapper that renders the
  cluster template (`new-cluster`) and the app template (`new-app`).

Consumers pin all of it at a **release tag**. The library does not track who
pins what — each consumer records its own pin sites, and its vendored copies in
its own `scripts/vendored-manifest.yml`, gated against the offer list
[scripts/vendorable-paths.yml](scripts/vendorable-paths.yml).

Start with [README.md](README.md), then
[docs/INCLUDE-CONTRACT.md](docs/INCLUDE-CONTRACT.md) and
[docs/VERSIONING.md](docs/VERSIONING.md).

## Golden rules

- **Never push to `main`.** Every change ships via a feature branch + merge
  request. Do not tag — the release job cuts tags from commit subjects.
- **No secrets in git**, ever. Templates reference `op://` / CI variables; they
  never embed credentials.
- **No AI/Claude attribution** anywhere (commits, MR text, code, docs). Commit
  style: `type(scope): summary`.
- **No Renovate** anywhere — the whole family bumps versions manually.
- **Preserve consumer parity.** The CI templates' input DEFAULTS reproduce
  weisssrv's current values; changing a default is a behavior change for
  weisssrv and must be flagged. When you change a template, update its parity
  note and its input list in the include contract.
- **The cluster template is the product; `weisssrv` is one instance of it.**
  A generic improvement — a gate, a role behaviour, a CI job, a doc pattern —
  lands here or in the cluster template first and reaches the running cluster
  through a tag and a pin bump. Fixing it only in `weisssrv` leaves every future
  cluster with the old shape and the next re-vendor reverts the edit.
- **Site data is never a default.** Domains, IPs, hostnames, pool names and
  credentials are inputs, asserted at role entry. A default that names one site
  is a bug even when it happens to be correct. The few shipped exceptions are
  written down with their reasoning — the collection README's exceptions table
  for role defaults, and the `cpu_selector` notes in
  [docs/INCLUDE-CONTRACT.md](docs/INCLUDE-CONTRACT.md) for CI inputs. A new one
  needs the same written justification.
- **The commit subject decides the version.** A breaking change written as
  `fix:` ships as a patch and consumers get it unannounced; write `feat!:`.

## Local gates

The canonical command list lives in
[README.md § Local gates](README.md#local-gates) — run that set, do not maintain
a second copy here. The library's own `.gitlab-ci.yml` runs the same gates in CI
by including its own templates (`include: local:`), so a template change is
rendered and executed by the MR that makes it. Keep it that way: a new job here
should be a new template plus an include, not an inline job.

## Editing CI templates

- Templates are GitLab `spec:inputs` files: a `spec:` header document, then
  `---`, then the config that interpolates `$[[ inputs.<name> ]]`. They must
  begin with `spec:` (no leading `---`) — that is why `lint/yamllint-self.yml`
  disables `document-start`.
- Interpolate an **array** input only in value position (`tags: $[[ inputs.tags
  ]]`), never mid-string.
- A value that can contain `<`, `>` or `|` (a pip version ceiling, a shell
  fragment) must be routed through a job `variables:` entry, not interpolated
  into the script line: `$[[ inputs.* ]]` is textual substitution, so the shell
  would parse `<` as a redirection before any expansion.
- A branch name in a `rules:if` string must be a literal (`default_branch`,
  `release_branch`). GitLab does not expand variables inside the quotes, so
  `$CI_DEFAULT_BRANCH` is compared as text and never matches.
- Keep the non-root vs root runner split intact (e.g. `flux-lint` branches on
  `substitute`): a job that assumes root (`apt-get`, `/usr/local/bin`) breaks on
  the shared tenant runner (non-privileged, UID 1000).

## Editing collection roles

- Every role variable carries the role name as a prefix; that name is
  consumer-visible API, so a rename is a breaking change and belongs in
  `MIGRATING.md` in the same MR.
- A role directory with a molecule scenario and no entry in the CI matrix fails
  the pipeline by design. Adding a role means adding its matrix row.
- A download is a `get_url`, or a `uri` that writes a `dest`. Every one carries
  the bounded-retry shape (`register`, `until: ... is succeeded`, `retries: 3`,
  `delay: 5`, `timeout: 60`) with its checksum pin; `tests/test_role_download_retry.py`
  walks every role for it.
- A negative case driven from converge.yml sits in a block tagged
  `molecule-idempotence-notest`, so the idempotence replay never re-fires it,
  and its guard is declared in the scenario's `expected-junit-failures.txt`
  with a ` ::<n>` count of hosts x cases; a rescued failure with no declaration
  passes `--strict` silently.
- Do not run molecule locally as a gate — CI is the arbiter. `ansible-lint`
  (production profile, repo root on `ANSIBLE_COLLECTIONS_PATH`) and `yamllint`
  are the local checks that mean something.
- CRITICAL: never run `molecule` (any subcommand, `syntax` included) or
  `ansible-galaxy collection install` with this repo's root FIRST on
  `ANSIBLE_COLLECTIONS_PATH`. Molecule's dependency step installs into the
  first path, and galaxy removes the existing `ansible_collections/weisssrv/infra`
  there — the live source tree — before it copies anything. Put a scratch
  directory first (`ANSIBLE_COLLECTIONS_PATH=/tmp/collections:$PWD`) or use
  `ansible-playbook --syntax-check` for a parse proof.
- A metric name a role emits is API too: alerts, promtool tests and dashboards
  bind to the literal string and none of them live in this repo.

## Comments

Comments state the current constraint and why it exists. They do not carry
history: no dates, no MR or pipeline numbers, no "this used to…", no incident
narration, no site hostnames. Long rationale belongs in the role or template
README, not in a 20-line header above five lines of code.

A comment block runs to three content lines. A block that guards a trap causing
an outage may open `CRITICAL:` and run to eight — that marker is for outages,
not for emphasis or for a long file header. `scripts/check-comment-length.py`
enforces both over the whole tree and blocks the pipeline; docs/SCRIPTS.md
carries its contract.
