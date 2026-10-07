#!/usr/bin/env bash
# Fail when the CI molecule/integration matrix and the scenario dirs on disk
# disagree, a role has no scenario, or the matrix exceeds MAX_MATRIX_ENTRIES.
# Contract + env: weisssrv-lib docs/SCRIPTS.md - check-molecule-matrix-coverage.sh.

set -euo pipefail

# Resolve repo root from this script's location so it works from any CWD
# (CI runs from $CI_PROJECT_DIR; local invocations may run from anywhere).
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)

cd "$REPO_ROOT"

CI_FILE="${CI_FILE:-.gitlab-ci.yml}"
# `-` not `:-`: an explicitly empty ROLES_DIR is the roles-less declaration and
# must survive, while an unset one still takes the default.
ROLES_DIR="${ROLES_DIR-ansible/roles}"
INTEGRATION_DIR="${INTEGRATION_DIR-ansible/integration-tests}"
MOLECULE_JOB="${MOLECULE_JOB:-molecule-tests}"
INTEGRATION_JOB="${INTEGRATION_JOB:-integration-tests}"
MAX_MATRIX_ENTRIES="${MAX_MATRIX_ENTRIES:-45}"
UNTESTED_ROLES="${UNTESTED_ROLES:-}"

export SCRIPT_DIR CI_FILE ROLES_DIR INTEGRATION_DIR MOLECULE_JOB INTEGRATION_JOB \
       MAX_MATRIX_ENTRIES UNTESTED_ROLES

python3 - <<'PYEOF'
import os
import sys
from pathlib import Path

import yaml

sys.path.insert(0, os.environ["SCRIPT_DIR"])

try:
    from ci_yaml import CILoader  # noqa: E402
except ImportError:
    print(
        "ERROR: ci_yaml.py must sit next to this script — vendor both "
        "(see weisssrv-lib scripts/vendorable-paths.yml).",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

CI_FILE = os.environ["CI_FILE"]
ROLES_DIR = os.environ["ROLES_DIR"]
INTEGRATION_DIR = os.environ["INTEGRATION_DIR"]
MOLECULE_JOB = os.environ["MOLECULE_JOB"]
INTEGRATION_JOB = os.environ["INTEGRATION_JOB"]
MAX_MATRIX_ENTRIES = int(os.environ["MAX_MATRIX_ENTRIES"])
# Roles intentionally shipped without molecule coverage. Empty by default; a
# consumer names a role here only with a rationale in its CI/taskfile call.
UNTESTED_ROLES = set(os.environ["UNTESTED_ROLES"].split())

repo = Path(".")
ci_path = repo / CI_FILE
with ci_path.open() as f:
    ci = yaml.load(f, Loader=CILoader)

if not isinstance(ci, dict):
    sys.stderr.write(f"ERROR: {CI_FILE} is not a YAML mapping\n")
    sys.exit(2)


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

# An explicitly empty ROLES_DIR declares "no in-repo roles": the molecule half
# is off, the integration half still runs. A NON-EMPTY path that does not
# resolve is a typo, so it still exits 2 rather than passing trivially.
ROLES_LESS = ROLES_DIR == ""

# On disk: <roles>/<role>/molecule/<scenario>/ — a dir holding a molecule.yml is
# the authoritative marker of a runnable scenario.
disk_molecule = set()
untested_roles = []
if not ROLES_LESS:
    roles_dir = repo / ROLES_DIR
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

    # Roles with NO runnable molecule scenario at all: a new role committed
    # without molecule/ never appears in disk_molecule, so the matrix diff
    # alone cannot catch it.
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

# An explicitly empty INTEGRATION_DIR declares "no integration suite"; a
# NON-EMPTY path that does not resolve is a typo, so it exits 2.
INTEGRATION_LESS = INTEGRATION_DIR == ""

if ROLES_LESS and INTEGRATION_LESS:
    sys.stderr.write(
        'ERROR: ROLES_DIR="" and INTEGRATION_DIR="" disable both halves — '
        "the gate would check nothing\n"
    )
    sys.exit(2)

# On disk: <integration-tests>/<name>/ holding molecule/*/molecule.yml. The CI
# job runs `cd <integration-tests>/$TEST && molecule test`, so the identifier is
# the directory name.
disk_integration = set()
if not INTEGRATION_LESS:
    it_dir = repo / INTEGRATION_DIR
    if not it_dir.is_dir():
        sys.stderr.write(
            f"ERROR: integration directory {INTEGRATION_DIR!r} does not exist\n"
        )
        sys.exit(2)
    for d in sorted(it_dir.iterdir()):
        if d.is_dir() and any((d / "molecule").glob("*/molecule.yml")):
            disk_integration.add(d.name)

# Both halves can be enabled and still hold nothing: directories present but
# empty of molecule.yml, and both matrices empty. Every set comparison below is
# then trivially clean, so the gate would pass having inspected no subject.
if not (disk_molecule or ci_molecule or disk_integration or ci_integration):
    enabled = []
    if not ROLES_LESS:
        enabled.append(f"{ROLES_DIR}/")
    if not INTEGRATION_LESS:
        enabled.append(f"{INTEGRATION_DIR}/")
    sys.stderr.write(
        "ERROR: the gate inspected nothing — no molecule.yml under "
        + " or ".join(enabled)
        + f" and no {MOLECULE_JOB}/{INTEGRATION_JOB} matrix entry in {CI_FILE}.\n"
        "  Point the gate at the real trees and job names, or declare a half off "
        'with ROLES_DIR="" / INTEGRATION_DIR="".\n'
    )
    sys.exit(2)

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

missing_molecule = [] if ROLES_LESS else sorted(disk_molecule - ci_molecule)
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

stale_molecule = [] if ROLES_LESS else sorted(ci_molecule - disk_molecule)
if stale_molecule:
    failed = True
    sys.stderr.write(
        f"ERROR: {MOLECULE_JOB} matrix entr(ies) with no scenario on disk "
        "(the job would fail at runtime):\n\n"
    )
    for role, scenario in stale_molecule:
        sys.stderr.write(
            f"  - ROLE: {role} / SCENARIO: {scenario} "
            f"(no {ROLES_DIR}/{role}/molecule/{scenario}/)\n"
        )
    sys.stderr.write(
        f"\n  Drop the entry from {CI_FILE} or restore the scenario.\n\n"
    )

missing_integration = [] if INTEGRATION_LESS else sorted(disk_integration - ci_integration)
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

stale_integration = [] if INTEGRATION_LESS else sorted(ci_integration - disk_integration)
if stale_integration:
    failed = True
    sys.stderr.write(
        f"ERROR: {INTEGRATION_JOB} matrix entr(ies) with no test dir on disk "
        "(the job would fail at runtime):\n\n"
    )
    for name in stale_integration:
        sys.stderr.write(f"  - TEST: {name} (no {INTEGRATION_DIR}/{name}/)\n")
    sys.stderr.write(
        f"\n  Drop the name from {CI_FILE} or restore the test dir.\n\n"
    )

if not ROLES_LESS and len(ci_molecule) > MAX_MATRIX_ENTRIES:
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

if ROLES_LESS:
    molecule_half = 'ROLES_DIR="": no in-repo roles, molecule half disabled'
else:
    molecule_half = (
        f"Molecule matrix and the {len(disk_molecule)} scenario dir(s) agree; "
        f"every role has at least one scenario; matrix size "
        f"{len(ci_molecule)}/{MAX_MATRIX_ENTRIES}"
    )
if INTEGRATION_LESS:
    integration_half = (
        'INTEGRATION_DIR="": no integration suite declared, integration half disabled'
    )
else:
    integration_half = (
        f"integration matrix and the {len(disk_integration)} test dir(s) agree"
    )
print(f"{molecule_half}; {integration_half}.")
PYEOF
