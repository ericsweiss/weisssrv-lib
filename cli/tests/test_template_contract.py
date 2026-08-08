"""Binds the bundled scaffold fixture to the real app-template repo.

The CLI hardcodes the template's layout: directory paths, manifest filenames,
kustomization opt-in lines, document names, commented opt-in markers and the
placeholder tokens. Nothing else checks that those literals still describe the
template, so a template change could silently break every scaffold run.

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
import subprocess
from pathlib import Path

import pytest

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
    _TEMPLATE is None,
    reason="no template checkout (set WEISSSRV_TEMPLATE_ROOT)",
)


def _fixture_files() -> list[str]:
    return sorted(
        str(p.relative_to(_FIXTURE)) for p in _FIXTURE.rglob("*") if p.is_file()
    )


def _flux_names(root: Path) -> set[str]:
    return {p.name for p in (root / tree.FLUX_DIR).glob("*.yaml")}


def _assert_cli_contract(root: Path) -> None:
    """Every literal the CLI depends on, checked against a scaffold tree."""
    flux = tree.flux_dir(root)
    assert flux.is_dir(), f"{tree.FLUX_DIR} missing"

    ktext = tree.flux_file(root, tree.KUSTOMIZATION).read_text(encoding="utf-8")
    active = set(kz.list_resources(ktext))
    assert active == set(_ACTIVE_RESOURCES)

    for name in tree.OPT_IN_MANIFESTS:
        assert (flux / name).exists(), f"opt-in manifest {name} missing"
        assert name not in active, f"{name} must ship commented out"
        assert kz.uncomment_resource(ktext, name)[1], f"no `# - {name}` opt-in line"

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

    # prune external-ingress keys off the `-internal` name suffix, and refuses
    # unless `wire internal-ingress` can activate an internal variant first.
    for name in ("ingressroute.yaml", "certificate.yaml"):
        text = tree.flux_file(root, name).read_text(encoding="utf-8")
        docs = tree.read_document_names(tree.flux_file(root, name))
        assert any(not d.endswith("-internal") for d in docs), f"{name}: no public doc"
        assert any(
            line.strip() == "# ---" for line in text.splitlines()
        ), f"{name}: no commented internal block (`# ---` marker)"

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
        """The template VENDORS this script. Prose said so; nothing checked it.

        The two copies had already drifted (98b6410f vs 83ad4b82) with every
        gate green, because a vendored file is only compared by whoever
        remembers to compare it. The cost is one-directional and quiet: fixes
        land here, the template keeps shipping the old script, and every project
        scaffolded from it inherits bugs that were fixed upstream months ago.

        Compared against the library AT THE REF THE TEMPLATE PINS, not at HEAD.
        Pinning is the whole point of vendoring — the template is entitled to
        lag, so long as it lags coherently. Comparing to HEAD would red this
        suite for the duration of every unreleased change, and a gate that is
        red by default gets muted. This one goes red for exactly one reason:
        someone bumped the ref without re-vendoring the file it carries.
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
            # CI clones shallow and does not always carry tags, so a tag one
            # commit behind HEAD can still be unresolvable. Fetch just that tag
            # and retry rather than reporting drift that is really a clone
            # depth. Offline (a local run with no remote) this simply fails
            # again and falls through to the error below.
            subprocess.run(
                ["git", "fetch", "--quiet", "--depth", "1", "origin", "tag", pinned],
                cwd=_LIB_ROOT,
                capture_output=True,
            )
            blob = _show()
        if blob.returncode != 0:
            # Deliberately a failure, not a skip: "the tag was not in the
            # checkout" is indistinguishable from "the files match" once it is
            # a skip, and this gate exists because an invisible gap is what
            # let the copies drift.
            raise AssertionError(
                f"cannot read {rel} at {pinned} from this checkout "
                f"({blob.stderr.decode(errors='replace').strip()}). "
                "Fetch tags (GIT_DEPTH: 0) so the comparison can run."
            )

        assert vendored.read_bytes() == blob.stdout, (
            f"the template vendors {rel} but pins library {pinned}, and the two "
            f"differ. Re-vendor with: git -C {_LIB_ROOT} show {pinned}:{rel} > {vendored}"
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
    def test_fixture_satisfies_contract(self):
        _assert_cli_contract(_FIXTURE)

    @_needs_template
    def test_template_satisfies_contract(self):
        _assert_cli_contract(_TEMPLATE)
