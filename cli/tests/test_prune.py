"""Tests for the `prune` command."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
import yaml

from weisssrv_lib_cli import prune, tree
from weisssrv_lib_cli import kustomization as kz


def _flux(root: Path, name: str) -> Path:
    return root / "kubernetes" / "flux" / name


def _kustomization(root: Path) -> str:
    return _flux(root, "kustomization.yaml").read_text(encoding="utf-8")


def _docs(root: Path, name: str):
    return [d for d in yaml.safe_load_all(_flux(root, name).read_text()) if d]


class TestSecrets:
    def test_removes_manifest_and_reference(self, scaffold):
        prune.prune(scaffold, ["secrets"])
        assert not _flux(scaffold, "externalsecret.yaml").exists()
        assert "externalsecret.yaml" not in kz.list_resources(_kustomization(scaffold))

    def test_removes_deployment_env_block(self, scaffold):
        prune.prune(scaffold, ["secrets"])
        dep = _flux(scaffold, "deployment.yaml").read_text()
        assert "secretKeyRef" not in dep
        assert "changeme-app-secrets" not in dep
        # The rest of the deployment still parses.
        loaded = yaml.safe_load(dep)
        container = loaded["spec"]["template"]["spec"]["containers"][0]
        assert "env" not in container

    def test_keeps_unrelated_plain_env_var(self, scaffold):
        # A consumer that customized the scaffold with an extra plain env var
        # (no secretKeyRef) must keep it: `prune secrets` removes ONLY the env
        # entries bound to the pruned ExternalSecret's target Secret.
        dep_path = _flux(scaffold, "deployment.yaml")
        dep_path.write_text(
            dep_path.read_text(encoding="utf-8").replace(
                "          env:\n            - name: API_KEY\n",
                "          env:\n"
                "            - name: LOG_LEVEL\n"
                "              value: info\n"
                "            - name: API_KEY\n",
            ),
            encoding="utf-8",
        )
        prune.prune(scaffold, ["secrets"])
        dep = dep_path.read_text()
        # The secretKeyRef-backed entry is gone.
        assert "secretKeyRef" not in dep
        assert "changeme-app-secrets" not in dep
        assert "API_KEY" not in dep
        # The user's plain env var survives.
        container = yaml.safe_load(dep)["spec"]["template"]["spec"]["containers"][0]
        env = container["env"]
        assert [e["name"] for e in env] == ["LOG_LEVEL"]
        assert env[0]["value"] == "info"


class TestMetrics:
    def test_removes_servicemonitor(self, scaffold):
        prune.prune(scaffold, ["metrics"])
        assert not _flux(scaffold, "servicemonitor.yaml").exists()
        assert "servicemonitor.yaml" not in kz.list_resources(_kustomization(scaffold))

    def test_removes_observability_scrape_policy(self, scaffold):
        prune.prune(scaffold, ["metrics"])
        names = {(d.get("metadata") or {}).get("name") for d in _docs(scaffold, "networkpolicy.yaml")}
        assert "allow-scrape-from-observability" not in names
        # The other policies survive.
        assert "default-deny" in names
        assert "allow-ingress-from-traefik" in names


class TestReplicas:
    def test_pdb_removed(self, scaffold):
        prune.prune(scaffold, ["pdb"])
        assert not _flux(scaffold, "pdb.yaml").exists()
        assert "pdb.yaml" not in kz.list_resources(_kustomization(scaffold))

    def test_single_replica_sets_one_and_drops_pdb(self, scaffold):
        prune.prune(scaffold, ["single-replica"])
        assert not _flux(scaffold, "pdb.yaml").exists()
        dep = yaml.safe_load(_flux(scaffold, "deployment.yaml").read_text())
        assert dep["spec"]["replicas"] == 1


class TestExternalIngress:
    def test_fresh_scaffold_refuses_and_leaves_files_intact(self, scaffold):
        # On a fresh scaffold the only ACTIVE documents are the public route +
        # cert; dropping them would empty both files while they stay listed in
        # the kustomization. prune must REFUSE and touch nothing.
        before_ir = _flux(scaffold, "ingressroute.yaml").read_text()
        before_cert = _flux(scaffold, "certificate.yaml").read_text()
        with pytest.raises(prune.PruneError) as exc:
            prune.prune(scaffold, ["external-ingress"])
        assert "wire internal-ingress" in str(exc.value)
        assert _flux(scaffold, "ingressroute.yaml").read_text() == before_ir
        assert _flux(scaffold, "certificate.yaml").read_text() == before_cert

    def test_internal_only_workflow(self, scaffold):
        # The meaningful path: wire internal first, then drop the public docs —
        # leaving exactly the internal IngressRoute + Certificate active.
        from weisssrv_lib_cli import wire

        wire.wire(scaffold, ["internal-ingress"])
        prune.prune(scaffold, ["external-ingress"])
        routes = _docs(scaffold, "ingressroute.yaml")
        certs = _docs(scaffold, "certificate.yaml")
        assert [r["metadata"]["name"] for r in routes] == ["changeme-app-internal"]
        assert [c["metadata"]["name"] for c in certs] == ["changeme-app-tls-internal"]


class TestGenericAndErrors:
    def test_manifest_feature(self, scaffold):
        prune.prune(scaffold, ["manifest:servicemonitor"])
        assert not _flux(scaffold, "servicemonitor.yaml").exists()
        assert "servicemonitor.yaml" not in kz.list_resources(_kustomization(scaffold))

    def test_unknown_feature_raises(self, scaffold):
        with pytest.raises(prune.PruneError):
            prune.prune(scaffold, ["bogus"])

    def test_manifest_empty_name_raises(self, scaffold):
        with pytest.raises(prune.PruneError):
            prune.prune(scaffold, ["manifest:"])

    @pytest.mark.parametrize(
        "evil",
        [
            "manifest:../evil",
            "manifest:../../evil",
            "manifest:/etc/passwd",
            "manifest:sub/evil",
            "manifest:..",
        ],
    )
    def test_manifest_path_traversal_rejected(self, scaffold, evil):
        # A `manifest:` value that escapes kubernetes/flux/ must be refused with
        # NO deletion. Plant a victim one level up (kubernetes/) that the naive
        # `../victim` path would resolve to, and assert it survives.
        victim = scaffold / "kubernetes" / "victim.yaml"
        victim.write_text("i must survive\n")
        with pytest.raises(prune.PruneError):
            prune.prune(scaffold, [evil])
        assert victim.exists()
        # A valid feature after the evil one must also not have run (up-front
        # validation refuses the whole request before touching anything).
        with pytest.raises(prune.PruneError):
            prune.prune(scaffold, ["secrets", evil])
        assert _flux(scaffold, "externalsecret.yaml").exists()
        assert victim.exists()

    @pytest.mark.parametrize("feature", ["secrets", "metrics", "pdb", "single-replica", "hpa"])
    def test_idempotent(self, scaffold, feature):
        prune.prune(scaffold, [feature])
        second = prune.prune(scaffold, [feature])
        assert second == []

    def test_hpa_file_removed(self, scaffold):
        prune.prune(scaffold, ["hpa"])
        assert not _flux(scaffold, "hpa.yaml").exists()

    def test_image_build_removes_root_files(self, scaffold):
        (scaffold / "Dockerfile").write_text("FROM scratch\n")
        (scaffold / ".dockerignore").write_text("*\n")
        prune.prune(scaffold, ["image-build"])
        assert not (scaffold / "Dockerfile").exists()
        assert not (scaffold / ".dockerignore").exists()


def _ci_paths(root: Path) -> dict[str, Path]:
    return {
        "gitlab-ci": root / tree.GITLAB_CI,
        "ruleset": root / tree.GITLAB_CI_EXTRA[0],
        "workflows": root / tree.GITHUB_WORKFLOWS,
    }


class TestCiShape:
    """`prune ci:<shape>` must reproduce the template's scripts/select-ci.sh."""

    def test_gitlab_keeps_gitlab_drops_github(self, scaffold):
        prune.prune(scaffold, ["ci:gitlab"])
        p = _ci_paths(scaffold)
        assert p["gitlab-ci"].is_file()
        assert p["ruleset"].is_file()
        assert not p["workflows"].exists()
        # select-ci.sh rmdirs an emptied parent; .gitlab keeps its host metadata
        # only if it has any — the fixture's .gitlab holds just the ruleset.
        assert not (scaffold / ".github").exists()

    def test_github_keeps_workflows_drops_gitlab_ci_and_ruleset(self, scaffold):
        prune.prune(scaffold, ["ci:github"])
        p = _ci_paths(scaffold)
        assert not p["gitlab-ci"].exists()
        assert not p["ruleset"].exists()
        assert sorted(x.name for x in p["workflows"].iterdir()) == [
            "build-image.yml",
            "ci.yml",
        ]
        # .gitlab held only the ruleset, so it is gone too.
        assert not (scaffold / ".gitlab").exists()

    def test_none_drops_both(self, scaffold):
        prune.prune(scaffold, ["ci:none"])
        for path in _ci_paths(scaffold).values():
            assert not path.exists()
        assert not (scaffold / ".github").exists()
        assert not (scaffold / ".gitlab").exists()

    @pytest.mark.parametrize(
        "shape,break_it",
        [
            ("gitlab", lambda r: (r / tree.GITLAB_CI).unlink()),
            ("github", lambda r: shutil.rmtree(r / tree.GITHUB_WORKFLOWS)),
        ],
    )
    def test_keeping_a_shape_the_tree_lacks_is_refused(self, scaffold, shape, break_it):
        # Otherwise the kept shape is already gone, the other is deleted, and the
        # project is left with NO pipeline while prune reports success.
        break_it(scaffold)
        other = tree.GITHUB_WORKFLOWS if shape == "gitlab" else tree.GITLAB_CI
        with pytest.raises(prune.PruneError, match="does not have"):
            prune.prune(scaffold, [f"ci:{shape}"])
        assert (scaffold / other).exists()

    def test_keeping_github_through_a_symlinked_parent_is_refused(
        self, scaffold, tmp_path
    ):
        # The DROP paths were already ancestor-checked; the KEPT shape was not.
        # A symlinked .github resolves to workflows outside the repo, which would
        # satisfy the keep-check and then delete the working GitLab pipeline.
        outside = tmp_path / "outside"
        (outside / "workflows").mkdir(parents=True)
        (outside / "workflows" / "ci.yml").write_text("name: x\n")
        shutil.rmtree(scaffold / ".github")
        (scaffold / ".github").symlink_to(outside, target_is_directory=True)
        with pytest.raises(prune.PruneError, match="symlinked"):
            prune.prune(scaffold, ["ci:github"])
        assert (scaffold / tree.GITLAB_CI).is_file()

    def test_keeping_gitlab_whose_config_is_a_symlink_is_refused(
        self, scaffold, tmp_path
    ):
        real = tmp_path / "elsewhere.yml"
        real.write_text("stages: [x]\n")
        (scaffold / tree.GITLAB_CI).unlink()
        (scaffold / tree.GITLAB_CI).symlink_to(real)
        with pytest.raises(prune.PruneError, match="does not have"):
            prune.prune(scaffold, ["ci:gitlab"])
        assert (scaffold / tree.GITHUB_WORKFLOWS).is_dir()

    def test_conflicting_shapes_are_refused_without_deleting_either(self, scaffold):
        # Applied in sequence these drop each other's files and leave the project
        # with NO CI — the most destructive outcome, from a request that cannot
        # have been meant.
        with pytest.raises(prune.PruneError, match="conflicting CI shapes"):
            prune.prune(scaffold, ["ci:gitlab", "ci:github"])
        assert (scaffold / tree.GITLAB_CI).is_file()
        assert (scaffold / tree.GITHUB_WORKFLOWS).is_dir()

    def test_repeating_one_shape_is_still_allowed(self, scaffold):
        prune.prune(scaffold, ["ci:gitlab", "ci:gitlab"])
        assert (scaffold / tree.GITLAB_CI).is_file()
        assert not (scaffold / tree.GITHUB_WORKFLOWS).exists()

    def test_symlinked_ci_parent_is_refused_and_nothing_outside_is_deleted(
        self, scaffold, tmp_path
    ):
        # The leaf guard already unlinks a symlinked target rather than
        # following it. An ANCESTOR was the hole: with `.github` a symlink,
        # root/".github/workflows" resolves outside the project and rmtree()
        # would delete whatever is there.
        outside = tmp_path / "outside"
        (outside / "workflows").mkdir(parents=True)
        (outside / "workflows" / "keep.yml").write_text("keep\n")
        shutil.rmtree(scaffold / ".github")
        (scaffold / ".github").symlink_to(outside, target_is_directory=True)

        with pytest.raises(prune.PruneError, match="symlinked"):
            prune.prune(scaffold, ["ci:gitlab"])

        assert (outside / "workflows" / "keep.yml").is_file()
        # Refused during validation, so the OTHER shape's files are untouched
        # too — a partial prune is worse than none.
        assert (scaffold / tree.GITLAB_CI).is_file()

    def test_non_ci_gitlab_metadata_survives(self, scaffold):
        # `.gitlab/{issue,merge_request}_templates/` are GitLab HOST metadata,
        # not CI: select-ci.sh leaves them, so the parent must NOT be rmdir'd.
        keep = scaffold / ".gitlab" / "issue_templates" / "Bug.md"
        keep.parent.mkdir(parents=True)
        keep.write_text("bug\n")
        prune.prune(scaffold, ["ci:none"])
        assert keep.is_file()
        assert not (scaffold / tree.GITLAB_CI_EXTRA[0]).exists()

    @pytest.mark.parametrize("shape", prune.CI_SHAPES)
    def test_kubernetes_flux_is_never_touched(self, scaffold, tmp_path, shape):
        # One FRESH tree per shape. Chaining the shapes onto a single tree used
        # to work here, but that sequence is now refused for the reason it
        # should be: the second call keeps a shape the first one deleted.
        tree_copy = tmp_path / f"scaffold-{shape}"
        shutil.copytree(scaffold, tree_copy)
        before = {
            p.name: p.read_bytes()
            for p in (tree_copy / "kubernetes" / "flux").glob("*.yaml")
        }
        prune.prune(tree_copy, [f"ci:{shape}"])
        after = {
            p.name: p.read_bytes()
            for p in (tree_copy / "kubernetes" / "flux").glob("*.yaml")
        }
        assert before == after

    @pytest.mark.parametrize("shape", ["gitlab", "github", "none"])
    def test_idempotent(self, scaffold, shape):
        prune.prune(scaffold, [f"ci:{shape}"])
        assert prune.prune(scaffold, [f"ci:{shape}"]) == []

    def test_reports_what_it_removed(self, scaffold):
        changed = prune.prune(scaffold, ["ci:github"])
        names = {str(p.relative_to(scaffold)) for p in changed}
        assert tree.GITLAB_CI in names
        assert tree.GITLAB_CI_EXTRA[0] in names


class TestCiShapeAllowlistSecurity:
    """The `ci:` selector deletes paths OUTSIDE kubernetes/flux, so it cannot
    lean on `_safe_manifest_name`'s containment guard. Its defence is that the
    shape name is only ever a dict KEY: no user text is ever joined into a path.
    """

    def test_allowlist_is_closed_over_fixed_template_paths(self):
        allowed = {rel for paths in prune._CI_SHAPE_DROPS.values() for rel in paths}
        assert allowed == {
            tree.GITLAB_CI,
            tree.GITHUB_WORKFLOWS,
            *tree.GITLAB_CI_EXTRA,
        }
        for rel in allowed:
            assert not os.path.isabs(rel), rel
            assert ".." not in Path(rel).parts, rel
        assert prune.CI_SHAPES == ("gitlab", "github", "none")

    @pytest.mark.parametrize(
        "evil",
        [
            "ci:",
            "ci:.",
            "ci:..",
            "ci:/etc",
            "ci:../../etc",
            "ci:gitlab/../../..",
            "ci:none/../gitlab",
            "ci:GITLAB",
            "ci:gitlab ",
            "ci:gitlab\x00",
            "ci:.gitlab-ci.yml",
            "ci:kubernetes/flux",
        ],
    )
    def test_crafted_shape_deletes_nothing(self, scaffold, tmp_path, evil):
        outside = tmp_path / "outside.txt"
        outside.write_text("i must survive\n")
        before = sorted(str(p.relative_to(scaffold)) for p in scaffold.rglob("*"))

        with pytest.raises(prune.PruneError) as exc:
            prune.prune(scaffold, [evil])
        assert "unknown CI shape" in str(exc.value)

        assert outside.exists()
        assert sorted(str(p.relative_to(scaffold)) for p in scaffold.rglob("*")) == before

        # And up-front validation refuses the whole request: a valid feature
        # queued before the crafted shape must not have run either.
        with pytest.raises(prune.PruneError):
            prune.prune(scaffold, ["secrets", evil])
        assert _flux(scaffold, "externalsecret.yaml").exists()
        assert outside.exists()

    def test_symlinked_workflows_dir_is_unlinked_not_followed(self, scaffold, tmp_path):
        # A planted symlink must not become a delete of whatever it points at.
        victim_dir = tmp_path / "victim"
        victim_dir.mkdir()
        (victim_dir / "precious.txt").write_text("keep me\n")

        workflows = scaffold / tree.GITHUB_WORKFLOWS
        import shutil as _sh

        _sh.rmtree(workflows)
        workflows.symlink_to(victim_dir, target_is_directory=True)

        prune.prune(scaffold, ["ci:none"])
        assert not workflows.exists() and not workflows.is_symlink()
        assert (victim_dir / "precious.txt").read_text() == "keep me\n"


class TestUpFrontValidation:
    def test_bad_name_after_valid_does_not_mutate(self, scaffold):
        # A typo AFTER a valid feature must raise before touching any file.
        with pytest.raises(prune.PruneError):
            prune.prune(scaffold, ["secrets", "bogus"])
        assert _flux(scaffold, "externalsecret.yaml").exists()
        assert "externalsecret.yaml" in kz.list_resources(_kustomization(scaffold))

    def test_external_ingress_guard_refuses_before_earlier_feature(self, scaffold):
        # metrics is valid, but external-ingress would empty a file on the fresh
        # scaffold — the whole request must refuse without pruning metrics.
        with pytest.raises(prune.PruneError):
            prune.prune(scaffold, ["metrics", "external-ingress"])
        assert _flux(scaffold, "servicemonitor.yaml").exists()
