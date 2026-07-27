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
    def test_flux_manifest_set_matches(self):
        assert _flux_names(_FIXTURE) == _flux_names(_TEMPLATE)

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
