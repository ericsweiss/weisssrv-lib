#!/usr/bin/env bash
# Guard against the molecule / integration-test CI matrix silently drifting
# from the scenario directories on disk.
#
# Two parallel:matrix blocks in the CI file enumerate which tests run:
#
#   molecule-tests    — ROLE/SCENARIO pairs, one per <roles>/*/molecule/*/
#   integration-tests — a TEST list, one per <integration-tests>/*/
#
# Environment overrides (all optional):
#   CI_FILE, ROLES_DIR, INTEGRATION_DIR, MOLECULE_JOB, INTEGRATION_JOB
#   UNTESTED_ROLES      space-separated roles allowed to ship with no scenario
#   MAX_MATRIX_ENTRIES  cap on molecule-matrix size (default 45)
#
# A new molecule scenario dir (or a new integration-tests dir) that nobody
# adds to the matrix runs in NO CI job — a brand-new role's tests would
# silently never execute, and the gap is invisible at review time. This check
# fails loudly when a scenario/test exists on disk with no matching matrix
# entry, naming the missing entries and where to add them. It ALSO fails when
# a role under ansible/roles/ has no molecule scenario at all (a role committed
# without molecule/ would otherwise ship permanently untested), unless the role
# is named in the UNTESTED_ROLES allowlist with a rationale.
#
# It also caps the molecule matrix at MAX_MATRIX_ENTRIES: an aggregate job that
# `needs:` every matrix entry hits GitLab's hard 50-needs-per-job limit and
# breaks pipeline creation outright, so the cap fails while there is headroom.
#
# Direction is deliberately one-way: we flag on-disk scenarios MISSING from
# the matrix (the dangerous drift — untested code). A matrix entry pointing at
# a now-deleted scenario dir is caught at runtime (molecule errors on a missing
# scenario), so we don't duplicate that here.
#
# Delegates the actual parsing to a small embedded Python program (PyYAML) so
# the matrix is read structurally — a `grep` over job rules would mis-match
# ROLE:/SCENARIO: strings that appear in comments or unrelated blocks.

set -euo pipefail

# Resolve repo root from this script's location so it works from any CWD
# (CI runs from $CI_PROJECT_DIR; local invocations may run from anywhere).
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)

cd "$REPO_ROOT"

CI_FILE="${CI_FILE:-.gitlab-ci.yml}"
ROLES_DIR="${ROLES_DIR:-ansible/roles}"
INTEGRATION_DIR="${INTEGRATION_DIR:-ansible/integration-tests}"
MOLECULE_JOB="${MOLECULE_JOB:-molecule-tests}"
INTEGRATION_JOB="${INTEGRATION_JOB:-integration-tests}"
MAX_MATRIX_ENTRIES="${MAX_MATRIX_ENTRIES:-45}"
UNTESTED_ROLES="${UNTESTED_ROLES:-}"

export CI_FILE ROLES_DIR INTEGRATION_DIR MOLECULE_JOB INTEGRATION_JOB \
       MAX_MATRIX_ENTRIES UNTESTED_ROLES

python3 - <<'PYEOF'
import os
import sys
from pathlib import Path

import yaml

CI_FILE = os.environ["CI_FILE"]
ROLES_DIR = os.environ["ROLES_DIR"]
INTEGRATION_DIR = os.environ["INTEGRATION_DIR"]
MOLECULE_JOB = os.environ["MOLECULE_JOB"]
INTEGRATION_JOB = os.environ["INTEGRATION_JOB"]
MAX_MATRIX_ENTRIES = int(os.environ["MAX_MATRIX_ENTRIES"])
# Roles intentionally shipped without molecule coverage. Empty by default; a
# consumer names a role here only with a rationale in its CI/taskfile call.
UNTESTED_ROLES = set(os.environ["UNTESTED_ROLES"].split())


def tag_passthrough(loader, tag_suffix, node):
    # Custom YAML tags (e.g. !reference) appear in .gitlab-ci.yml. Preserve the
    # underlying scalar/sequence/mapping so a tagged node inside (or near) a
    # matrix block keeps its structure instead of collapsing to None and
    # producing a false "missing scenario" failure. We don't resolve GitLab's
    # !reference semantics — only keep the parse structurally intact.
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return None


yaml.SafeLoader.add_multi_constructor("!", tag_passthrough)

repo = Path(".")
ci_path = repo / CI_FILE
with ci_path.open() as f:
    ci = yaml.safe_load(f)


def matrix_entries(job_name):
    """Return the list of dicts under <job>.parallel.matrix, or []."""
    job = ci.get(job_name)
    if not isinstance(job, dict):
        return []
    parallel = job.get("parallel", {})
    if not isinstance(parallel, dict):
        return []
    matrix = parallel.get("matrix", [])
    return matrix if isinstance(matrix, list) else []


# ---- molecule-tests: (ROLE, SCENARIO) pairs ----------------------------------
# Matrix shape: a list of {ROLE: <name>, SCENARIO: <name>} dicts.
ci_molecule = set()
for entry in matrix_entries(MOLECULE_JOB):
    if not isinstance(entry, dict):
        continue
    role = entry.get("ROLE")
    scenario = entry.get("SCENARIO")
    if isinstance(role, str) and isinstance(scenario, str):
        ci_molecule.add((role, scenario))

# On disk: ansible/roles/<role>/molecule/<scenario>/ (a dir containing a
# molecule.yml is the authoritative marker of a runnable scenario).
disk_molecule = set()
roles_dir = repo / ROLES_DIR
# Fail closed: a mistyped ROLES_DIR yields an empty on-disk set, which every
# coverage comparison below then passes trivially — silently disabling the
# gate. An absent roles dir is a configuration error, not "nothing to check".
if not roles_dir.is_dir():
    sys.stderr.write(f"ERROR: roles directory {ROLES_DIR!r} does not exist\n")
    sys.exit(2)
for scenario_dir in sorted(roles_dir.glob("*/molecule/*")):
    if not scenario_dir.is_dir():
        continue
    if not (scenario_dir / "molecule.yml").is_file():
        continue
    role = scenario_dir.parent.parent.name
    scenario = scenario_dir.name
    disk_molecule.add((role, scenario))

# Roles with NO runnable molecule scenario at all: a new role committed without
# molecule/ would never appear in disk_molecule, so the matrix diff alone can't
# catch it.
untested_roles = []
if roles_dir.is_dir():
    tested_roles = {role for role, _scenario in disk_molecule}
    for role_dir in sorted(roles_dir.iterdir()):
        if not role_dir.is_dir():
            continue
        if role_dir.name in UNTESTED_ROLES:
            continue
        if role_dir.name not in tested_roles:
            untested_roles.append(role_dir.name)

# ---- integration-tests: TEST list --------------------------------------------
# Matrix shape: a single {TEST: [a, b, ...]} entry (a list of test names).
ci_integration = set()
for entry in matrix_entries(INTEGRATION_JOB):
    if not isinstance(entry, dict):
        continue
    tests = entry.get("TEST")
    if isinstance(tests, list):
        ci_integration.update(t for t in tests if isinstance(t, str))
    elif isinstance(tests, str):
        ci_integration.add(tests)

# On disk: ansible/integration-tests/<name>/ where <name> contains a
# molecule/ subdir with at least one scenario molecule.yml. The CI job does
# `cd ansible/integration-tests/$TEST && molecule test` (default scenario), so
# the test identifier is the directory name <name>, not the scenario.
disk_integration = set()
it_dir = repo / INTEGRATION_DIR
if it_dir.is_dir():
    for d in sorted(it_dir.iterdir()):
        if d.is_dir() and any((d / "molecule").glob("*/molecule.yml")):
            disk_integration.add(d.name)

failed = False

if untested_roles:
    failed = True
    sys.stderr.write(
        "ERROR: role(s) with no molecule scenario (would ship permanently untested):\n\n"
    )
    for role in untested_roles:
        sys.stderr.write(f"  - {ROLES_DIR}/{role}/ (no molecule/*/molecule.yml)\n")
    sys.stderr.write(
        f"\n  Add a molecule scenario for the role (plus its {MOLECULE_JOB}\n"
        f"  matrix entry in {CI_FILE}), or — only with a rationale — name it in\n"
        "  the UNTESTED_ROLES environment allowlist.\n\n"
    )

missing_molecule = sorted(disk_molecule - ci_molecule)
if missing_molecule:
    failed = True
    sys.stderr.write(
        "ERROR: molecule scenario(s) on disk with no molecule-tests matrix entry:\n\n"
    )
    for role, scenario in missing_molecule:
        sys.stderr.write(f"  - {ROLES_DIR}/{role}/molecule/{scenario}/\n")
    sys.stderr.write(
        f"\n  Add a matching entry to the {MOLECULE_JOB} parallel:matrix in\n"
        f"  {CI_FILE}:\n"
        "      - ROLE: <role>\n"
        "        SCENARIO: <scenario>\n\n"
    )

missing_integration = sorted(disk_integration - ci_integration)
if missing_integration:
    failed = True
    sys.stderr.write(
        "ERROR: integration-test(s) on disk with no integration-tests matrix entry:\n\n"
    )
    for name in missing_integration:
        sys.stderr.write(f"  - {INTEGRATION_DIR}/{name}/\n")
    sys.stderr.write(
        f"\n  Add the name under the {INTEGRATION_JOB} parallel:matrix TEST list\n"
        f"  in {CI_FILE}.\n\n"
    )

if len(ci_molecule) > MAX_MATRIX_ENTRIES:
    failed = True
    sys.stderr.write(
        f"ERROR: the {MOLECULE_JOB} matrix has {len(ci_molecule)} entries, over the\n"
        f"  MAX_MATRIX_ENTRIES cap of {MAX_MATRIX_ENTRIES}. An aggregate job that\n"
        "  `needs:` every entry hits GitLab's hard 50-needs-per-job limit and breaks\n"
        "  pipeline creation. Split the matrix (or the aggregate job) before adding\n"
        "  more scenarios.\n\n"
    )

if failed:
    sys.exit(1)

print(
    f"Molecule matrix covers all {len(disk_molecule)} scenario dir(s); "
    f"integration matrix covers all {len(disk_integration)} test dir(s); "
    f"every role has at least one scenario; matrix size "
    f"{len(ci_molecule)}/{MAX_MATRIX_ENTRIES}."
)
PYEOF
