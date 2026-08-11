"""Binds the bundled scaffold fixture to the real app-template repo.

The CLI hardcodes the template's layout: directory paths, manifest filenames,
the `# - optional/<file>` opt-in lines, document names and the placeholder
tokens. Nothing else checks that those literals still describe the template, so
a template change could silently break every scaffold run.

These tests assert one contract against BOTH trees — the fixture (always) and a
real template checkout (when reachable) — plus byte-equality between them, so
drift fails here instead of at a consumer.

Point at a checkout with `WEISSSRV_TEMPLATE_ROOT=<path>`; otherwise a sibling
`weisssrv-app-template` / `weisssrv-project-template` of this library checkout
is used. With neither, the template half skips (the fixture half still runs).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import needs_optional_layout

from weisssrv_lib_cli import kustomization as kz, prune, tree, wire

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "scaffold"
_LIB_ROOT = _FIXTURE.parents[3]

# Every other fixture file models the template's CONTENT (the CLI parses or
# edits it) and must stay byte-identical. README.md is a synthetic stub instead:
# the template's own README is 260+ lines of prose that carries no placeholder
# token, so the fixture substitutes a minimal token-bearing file to exercise
# rename/verify outside kubernetes/flux.
_SYNTHETIC = {"README.md"}

# Resources the scaffold ships active in kustomization.yaml.
_ACTIVE_RESOURCES = frozenset(
    {
        "deployment.yaml",
        "service.yaml",
        "externalsecret.yaml",
        "certificate.yaml",
        "ingressroute.yaml",
        "networkpolicy.yaml",
        "servicemonitor.yaml",
        "prometheusrule.yaml",
        "vpa.yaml",
        "pdb.yaml",
    }
)

_ROOT_FILES = ("Dockerfile", ".dockerignore")  # prune image-build targets

# The template ships ALL THREE CI shapes (docs/CI-SHAPES.md) and a project keeps
# one; `prune ci:<shape>` is what selects. Every path the selector may delete
# must therefore be present in an unselected tree — otherwise a shape's files
# moved and the prune silently becomes a no-op.
_CI_SHAPE_PATHS = (tree.GITLAB_CI, tree.GITHUB_WORKFLOWS, *tree.GITLAB_CI_EXTRA)


def _is_template(path: Path) -> bool:
    return (path / tree.FLUX_DIR).is_dir()


def _template_root() -> Path | None:
    env = os.environ.get("WEISSSRV_TEMPLATE_ROOT")
    if env:
        cand = Path(env).expanduser().resolve()
        if not _is_template(cand):
            # An explicit pointer must never degrade to a silent skip.
            raise RuntimeError(
                f"WEISSSRV_TEMPLATE_ROOT={env} is not a template checkout"
            )
        return cand
    for name in ("weisssrv-app-template", "weisssrv-project-template"):
        cand = _LIB_ROOT.parent / name
        if _is_template(cand):
            return cand
    return None


_TEMPLATE = _template_root()
if _TEMPLATE is None and os.environ.get("WEISSSRV_TEMPLATE_REQUIRED"):
    # CI sets this so the cross-repo half can never silently skip there.
    raise RuntimeError(
        "WEISSSRV_TEMPLATE_REQUIRED is set but no template checkout was found"
    )
_needs_template = pytest.mark.skipif(
    _TEMPLATE is None
    or not (_TEMPLATE / tree.FLUX_DIR / tree.OPTIONAL_DIR).is_dir(),
    reason="no template checkout (set WEISSSRV_TEMPLATE_ROOT), or the checkout "
    f"predates {tree.FLUX_DIR}/{tree.OPTIONAL_DIR}/ — every fixture-vs-template "
    "comparison false-fails across that transition, so the re-synced fixture "
    "suite carries the contract until the template adopts the layout",
)


def _fixture_files() -> list[str]:
    return sorted(
        str(p.relative_to(_FIXTURE)) for p in _FIXTURE.rglob("*") if p.is_file()
    )


def _flux_names(root: Path) -> set[str]:
    """Every manifest under kubernetes/flux, as FLUX_DIR-relative paths.

    Recursive on purpose: the opt-in manifests moved into optional/, and a
    top-level-only glob would report a set that matches while that whole
    directory drifted.
    """
    flux = root / tree.FLUX_DIR
    return {str(p.relative_to(flux)) for p in flux.rglob("*.yaml")}


def _assert_cli_contract(root: Path) -> None:
    """Every literal the CLI depends on, checked against a scaffold tree."""
    flux = tree.flux_dir(root)
    assert flux.is_dir(), f"{tree.FLUX_DIR} missing"

    ktext = tree.flux_file(root, tree.KUSTOMIZATION).read_text(encoding="utf-8")
    active = set(kz.list_resources(ktext))
    assert active == set(_ACTIVE_RESOURCES)

    # Opt-in manifests are real files under optional/, switched off by leaving
    # their resource line commented. `wire` uncomments that exact line, so its
    # spelling — `optional/<file>` — is part of the contract.
    optional = tree.optional_dir(root)
    assert optional.is_dir(), f"{tree.FLUX_DIR}/{tree.OPTIONAL_DIR} missing"
    for name in tree.OPT_IN_MANIFESTS:
        assert name.startswith(f"{tree.OPTIONAL_DIR}/"), f"{name} is not an opt-in path"
        assert (flux / name).exists(), f"opt-in manifest {name} missing"
        assert name not in active, f"{name} must ship commented out"
        assert kz.uncomment_resource(ktext, name)[1], f"no `# - {name}` opt-in line"

    # optional/kustomization.yaml is what CI builds to validate the switched-off
    # manifests, so every file there must be listed in it (and `prune hpa`
    # removes an entry from it).
    opt_ktext = (optional / tree.KUSTOMIZATION).read_text(encoding="utf-8")
    opt_listed = set(kz.list_resources(opt_ktext))
    opt_on_disk = {
        p.name for p in optional.glob("*.yaml") if p.name != tree.KUSTOMIZATION
    }
    assert opt_on_disk == opt_listed, (
        f"{tree.OPTIONAL_DIR}/{tree.KUSTOMIZATION} lists {sorted(opt_listed)} but "
        f"the directory holds {sorted(opt_on_disk)}"
    )
    assert {n.rsplit("/", 1)[-1] for n in tree.OPT_IN_MANIFESTS} == opt_on_disk

    for name in (tree.DEPLOYMENT, "vpa.yaml", "pdb.yaml", "externalsecret.yaml",
                 "servicemonitor.yaml", "networkpolicy.yaml", "ingressroute.yaml",
                 "certificate.yaml"):
        assert (flux / name).exists(), f"{name} missing"

    for name in _ROOT_FILES:
        assert (root / name).exists(), f"prune image-build target {name} missing"

    # All three CI shapes ship unselected, and `prune ci:` can reach each one.
    for rel in _CI_SHAPE_PATHS:
        assert (root / rel).exists(), f"CI-shape path {rel} missing"
    assert (root / tree.GITHUB_WORKFLOWS).is_dir()
    assert any(
        p.is_file() for p in (root / tree.GITHUB_WORKFLOWS).iterdir()
    ), f"{tree.GITHUB_WORKFLOWS}/ ships no workflow"
    assert set(prune.CI_SHAPES) == {"gitlab", "github", "none"}

    # prune metrics drops this document from networkpolicy.yaml.
    npol = tree.flux_file(root, "networkpolicy.yaml").read_text(encoding="utf-8")
    assert "allow-scrape-from-observability" in tree.read_document_names(
        tree.flux_file(root, "networkpolicy.yaml")
    ), f"networkpolicy.yaml has no allow-scrape-from-observability document ({len(npol)}B)"

    # prune external-ingress keys off the `-internal` name suffix: the live
    # files carry the public documents, the optional/ variants carry the
    # internal ones, and it refuses until `wire internal-ingress` enables those.
    for name in ("ingressroute.yaml", "certificate.yaml"):
        docs = tree.read_document_names(tree.flux_file(root, name))
        assert docs, f"{name}: no document"
        assert not any(d.endswith("-internal") for d in docs), (
            f"{name}: an internal variant lives here, not in {tree.OPTIONAL_DIR}/"
        )
    for name in tree.INTERNAL_INGRESS_MANIFESTS:
        docs = tree.read_document_names(tree.flux_file(root, name))
        assert docs and all(d.endswith("-internal") for d in docs), (
            f"{name}: prune external-ingress keys off the `-internal` suffix"
        )

    # wire sso uncomments this exact middleware pair in the public route.
    ing = tree.flux_file(root, "ingressroute.yaml").read_text(encoding="utf-8")
    assert "# - name: authentik-auth" in ing
    assert "#   namespace: authentik" in ing

    # wire hpa drops spec.replicas and makes the VPA memory-only.
    dep = tree.load_yaml(tree.flux_file(root, tree.DEPLOYMENT))
    assert "replicas" in dep["spec"]
    vpa = tree.load_yaml(tree.flux_file(root, "vpa.yaml"))
    assert vpa["spec"]["resourcePolicy"]["containerPolicies"]

    assert set(prune.FEATURES) and set(wire.FEATURES)


def _pinned_lib_ref() -> str:
    """The one library ref the template pins — proven to be exactly one.

    The template pins the library in several places (include blocks plus the
    wrapper scripts). They must agree, or "the ref the template pins" is not a
    well-defined thing and `rename.sh` and `select-ci.sh` can run different
    versions of the same CLI.

    Every ref is captured RAW and then required to be a version, rather than
    matched with a version-shaped pattern. The difference matters: a pattern that
    only recognises `vX.Y.Z` cannot see `ref: main` or a SHA at all, so switching
    one include to a moving ref would be silently dropped from the set while the
    remaining pins still agree — a gate that passes precisely when the thing it
    guards has been broken.
    """
    pins: set[str] = set()
    ci = _TEMPLATE / ".gitlab-ci.yml"
    if ci.is_file():
        # Only refs belonging to weisssrv-lib includes: `ref:` under an include
        # for some OTHER project is none of this test's business.
        project = None
        for line in ci.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*-?\s*project:\s*(\S+)", line)
            if m:
                project = m.group(1).strip("'\"")
                continue
            m = re.match(r"\s*ref:\s*(\S+)\s*$", line)
            if m and project and project.endswith("weisssrv-lib"):
                pins.add(m.group(1).strip("'\""))
    for script in sorted((_TEMPLATE / "scripts").glob("*.sh")):
        pins |= set(
            re.findall(r"WEISSSRV_LIB_REF:-([^}\"'\s]+)", script.read_text(encoding="utf-8"))
        )
    assert pins, "the template pins no library ref at all"
    unversioned = sorted(p for p in pins if not re.fullmatch(r"v\d+\.\d+\.\d+", p))
    assert not unversioned, (
        "the template pins a moving library ref: "
        + ", ".join(unversioned)
        + " — a branch or SHA defeats the vendored-copy comparison below, which "
        "can only resolve a released tag"
    )
    assert len(pins) == 1, f"the template pins more than one library ref: {sorted(pins)}"
    return pins.pop()


class TestFixtureMatchesTemplate:
    def test_env_override_points_at_a_template(self):
        env = os.environ.get("WEISSSRV_TEMPLATE_ROOT")
        if env:
            assert _TEMPLATE is not None, (
                f"WEISSSRV_TEMPLATE_ROOT={env} has no {tree.FLUX_DIR}/"
            )

    @_needs_template
    @pytest.mark.parametrize("rel", [f for f in _fixture_files() if f not in _SYNTHETIC])
    def test_byte_identical(self, rel):
        fixture = (_FIXTURE / rel).read_bytes()
        real = _TEMPLATE / rel
        assert real.exists(), f"{rel} is gone from the template"
        assert fixture == real.read_bytes(), (
            f"{rel} drifted; resync with: cp {real} {_FIXTURE / rel}"
        )

    @_needs_template
    def test_vendored_semantic_release_matches_the_library_ref_it_pins(self):
        """The template vendors this script; nothing else compares the copies.

        Compared at the ref the template PINS, not at HEAD: the template is
        entitled to lag, so long as it lags coherently. The gate reds when
        someone bumps the ref without re-vendoring the file.
        """
        rel = "scripts/semantic-release.py"
        vendored = _TEMPLATE / rel
        assert vendored.is_file(), f"{rel} is no longer vendored in the template"

        pinned = _pinned_lib_ref()

        def _show():
            return subprocess.run(
                ["git", "show", f"{pinned}:{rel}"], cwd=_LIB_ROOT, capture_output=True
            )

        blob = _show()
        if blob.returncode != 0:
            # A shallow CI clone may not carry the tag: fetch just that tag
            # and retry, rather than reporting a clone depth as drift.
            subprocess.run(
                ["git", "fetch", "--quiet", "--depth", "1", "origin", "tag", pinned],
                cwd=_LIB_ROOT,
                capture_output=True,
            )
            blob = _show()
        if blob.returncode != 0:
            # A failure, not a skip: an unreadable tag would otherwise be
            # indistinguishable from "the files match".
            raise AssertionError(
                f"cannot read {rel} at {pinned} from this checkout "
                f"({blob.stderr.decode(errors='replace').strip()}). "
                "Fetch tags (GIT_DEPTH: 0) so the comparison can run."
            )

        assert vendored.read_bytes() == blob.stdout, (
            f"the template vendors {rel} but pins library {pinned}, and the two "
            f"differ. Re-vendor with: git -C {_LIB_ROOT} show {pinned}:{rel} > {vendored}"
        )

    def test_example_and_fixture_release_workflows_match(self):
        """The GitHub release workflow exists in three byte-identical places:
        the library's reference copy, the scaffold fixture, and the template's
        vendored .github/workflows/release.yml. This compares the two that live
        in THIS repo, so it deliberately runs without a template checkout.
        """
        rel = ".github/workflows/release.yml"
        example = _LIB_ROOT / "ci" / "release" / "github-release-workflow.example.yml"
        fixture = _FIXTURE / rel

        assert example.is_file(), "the library's reference copy is gone"
        assert fixture.is_file(), f"{rel} is missing from the scaffold fixture"
        assert example.read_bytes() == fixture.read_bytes(), (
            f"{rel} in the fixture has drifted from {example.name}; "
            f"resync with: cp {example} {fixture}"
        )

    @_needs_template
    def test_vendored_github_release_workflow_matches_the_library_example(self):
        """The third copy: what a consumer runs. With the fixture pinned to the
        example above, this closes the triangle. Compared against the working
        tree rather than a tag: the file is reference YAML a consumer copies by
        hand, so there is no pinned ref for it to lag behind.
        """
        rel = ".github/workflows/release.yml"
        example = _LIB_ROOT / "ci" / "release" / "github-release-workflow.example.yml"
        vendored = _TEMPLATE / rel

        assert example.is_file(), "the library's reference copy is gone"
        assert vendored.is_file(), f"{rel} is no longer vendored in the template"
        assert example.read_bytes() == vendored.read_bytes(), (
            f"{rel} in the template has drifted from {example.name}; "
            f"re-vendor with: cp {example} {vendored}"
        )

    @_needs_template
    def test_flux_manifest_set_matches(self):
        assert _flux_names(_FIXTURE) == _flux_names(_TEMPLATE)

    @_needs_template
    def test_fixture_covers_every_template_ci_file(self):
        """A template file absent from the fixture is compared by NOTHING.

        test_byte_identical parametrises over _fixture_files(), which walks the
        FIXTURE — so drift is only caught for files the fixture already has.
        Adding a workflow to the template and forgetting the fixture leaves it
        permanently unchecked, and the suite stays green. That is how
        release.yml arrived: vendored into the template, invisible here.

        Scoped to the CI surface rather than the whole tree: those are the files
        the byte-identity contract exists to protect.
        """
        ci_rel = {
            ".gitlab-ci.yml",
            ".gitlab/secret-detection-ruleset.toml",
            ".dockerignore",
            "Dockerfile",
            "CODEOWNERS",
            # ruff.toml is CI surface, not repo cosmetics: the python-lint
            # include passes no `config:` input, so ruff DISCOVERS this file,
            # and the same file backs `task python-lint` and the github shape's
            # step. A drifted copy changes what all three enforce.
            "ruff.toml",
        }
        for p in (_TEMPLATE / ".github" / "workflows").glob("*.y*ml"):
            ci_rel.add(str(p.relative_to(_TEMPLATE)))
        missing = sorted(
            r for r in ci_rel if (_TEMPLATE / r).is_file() and not (_FIXTURE / r).is_file()
        )
        assert not missing, (
            "template CI files with no fixture copy, so nothing compares them: "
            + ", ".join(missing)
        )

    @pytest.mark.parametrize("rel", sorted(_SYNTHETIC))
    def test_synthetic_files_carry_both_tokens(self, rel):
        text = (_FIXTURE / rel).read_text(encoding="utf-8")
        assert tree.APP_TOKEN in text and tree.GROUP_TOKEN in text

    @_needs_template
    def test_template_still_has_tokens_to_rename(self):
        found = set()
        for path in tree.tracked_files(_TEMPLATE):
            text = path.read_text(encoding="utf-8", errors="surrogateescape")
            found.update(t for t in (tree.APP_TOKEN, tree.GROUP_TOKEN) if t in text)
        assert found == {tree.APP_TOKEN, tree.GROUP_TOKEN}

    @_needs_template
    def test_template_calls_the_console_script(self):
        script = (_TEMPLATE / "scripts" / "rename.sh").read_text(encoding="utf-8")
        assert "weisssrv-new-project" in script


class TestCliContract:
    @needs_optional_layout
    def test_fixture_satisfies_contract(self):
        _assert_cli_contract(_FIXTURE)

    @_needs_template
    def test_template_satisfies_contract(self):
        _assert_cli_contract(_TEMPLATE)


class TestOptionalManifests:
    """The alternates used to ship as commented-out blocks, which nothing
    parsed, built or validated — so an opt-in could rot until the day someone
    enabled it. They are real files under optional/ now, and these tests are the
    translation of that old guarantee: nothing in the flux tree is a commented
    resource any more, and every optional manifest builds and validates.
    """

    @_needs_template
    def test_no_manifest_ships_a_commented_out_resource(self):
        # `# apiVersion:` and a commented `---` are what a commented-out
        # document looks like. The `# - optional/<file>` lines in the
        # kustomization are commented REFERENCES, not resources, and stay.
        offenders = []
        for rel in sorted(_flux_names(_TEMPLATE)):
            text = (_TEMPLATE / tree.FLUX_DIR / rel).read_text(encoding="utf-8")
            for i, line in enumerate(text.splitlines(), start=1):
                stripped = line.strip()
                if re.match(r"#\s*apiVersion:", stripped) or stripped == "# ---":
                    offenders.append(f"{rel}:{i}")
        assert not offenders, (
            "commented-out resources, which nothing parses, builds or validates: "
            + ", ".join(offenders)
            + f" — make each a real manifest under {tree.OPTIONAL_DIR}/ or delete it"
        )

    @_needs_template
    @pytest.mark.parametrize("name", sorted(tree.OPT_IN_MANIFESTS))
    def test_optional_manifest_is_a_real_document(self, name):
        docs = tree._safe_load_all(
            (_TEMPLATE / tree.FLUX_DIR / name).read_text(encoding="utf-8")
        )
        real = [d for d in docs if d]
        assert real, f"{name} yielded no document"
        for doc in real:
            assert doc.get("apiVersion") and doc.get("kind"), f"{name}: not a resource"
            assert (doc.get("metadata") or {}).get("name"), f"{name}: no metadata.name"

    @_needs_template
    def test_optional_dir_builds_and_validates(self):
        """`kustomize build optional/ | kubeconform` — the same pair CI runs, so
        a switched-off manifest cannot rot into one that fails when enabled."""
        for tool in ("kustomize", "kubeconform"):
            if not shutil.which(tool):
                pytest.skip(f"{tool} not on PATH")
        built = subprocess.run(
            ["kustomize", "build", str(_TEMPLATE / tree.FLUX_DIR / tree.OPTIONAL_DIR)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert built.returncode == 0, built.stderr
        checked = subprocess.run(
            ["kubeconform", "-strict", "-ignore-missing-schemas", "-summary", "-"],
            input=built.stdout,
            capture_output=True,
            text=True,
            check=False,
        )
        assert checked.returncode == 0, checked.stdout + checked.stderr
