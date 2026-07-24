"""Unit tests for the kustomize `resources:` list surgery.

These exercise kustomization.py directly (no scaffold fixture) so the
`resources:`-scoping and the append/anchor branches are covered explicitly.
"""
from __future__ import annotations

from weisssrv_lib_cli import kustomization as kz

_SCOPED = """\
---
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - deployment.yaml
  - service.yaml
  # - hpa.yaml   # opt-in
components:
  - service.yaml
  - ../base/common.yaml
"""


class TestScoping:
    def test_only_resources_block_listed(self):
        # The `components:` entries (including a path-like one) must NOT appear.
        assert kz.list_resources(_SCOPED) == ["deployment.yaml", "service.yaml"]

    def test_remove_only_touches_resources_block(self):
        new, changed = kz.remove_resource(_SCOPED, "service.yaml")
        assert changed
        # The resources-block service.yaml is gone; the components one survives.
        assert kz.list_resources(new) == ["deployment.yaml"]
        assert "  - service.yaml" not in new.split("components:")[0]
        assert "- service.yaml" in new.split("components:")[1]

    def test_add_appends_within_resources_block(self):
        new, changed = kz.add_resource(_SCOPED, "pdb.yaml")
        assert changed
        assert "pdb.yaml" in kz.list_resources(new)
        # Inserted inside resources, before the components: key.
        head, tail = new.split("components:")
        assert "- pdb.yaml" in head
        assert "- pdb.yaml" not in tail

    def test_uncomment_prefers_optin_line(self):
        new, changed = kz.add_resource(_SCOPED, "hpa.yaml")
        assert changed
        assert kz.list_resources(new).count("hpa.yaml") == 1


class TestAppendBranch:
    def test_append_when_no_active_resource(self):
        # last_idx is None branch: resources block has only a commented entry.
        text = "resources:\n  # - deployment.yaml\n"
        new, changed = kz.add_resource(text, "service.yaml")
        assert changed
        assert "service.yaml" in kz.list_resources(new)

    def test_append_preserves_missing_trailing_newline(self):
        text = "resources:\n  - deployment.yaml"  # no trailing newline
        new, changed = kz.add_resource(text, "service.yaml")
        assert changed
        assert kz.list_resources(new) == ["deployment.yaml", "service.yaml"]
        assert new.endswith("- service.yaml\n")

    def test_add_is_noop_when_already_active(self):
        text = "resources:\n  - deployment.yaml\n"
        new, changed = kz.add_resource(text, "deployment.yaml")
        assert not changed
        assert new == text


class TestEmptyOrAbsentResourcesBlock:
    """The `last_active_idx is None` path must keep the new item INSIDE
    `resources:` (never at EOF past a sibling key) and create the block when the
    file has none."""

    def test_commented_only_block_before_another_top_key(self):
        # resources: holds only an opt-in comment, and a components: key follows.
        # A naive EOF append would land the item under components:.
        text = (
            "resources:\n"
            "  # - hpa.yaml   # opt-in\n"
            "components:\n"
            "  - ../base/common.yaml\n"
        )
        new, changed = kz.add_resource(text, "service.yaml")
        assert changed
        assert kz.list_resources(new) == ["service.yaml"]
        head, tail = new.split("components:")
        assert "- service.yaml" in head  # under resources:, before components:
        assert "- service.yaml" not in tail  # NOT appended at EOF
        # The pre-existing opt-in comment and the components list survive intact.
        assert "# - hpa.yaml" in head
        assert "- ../base/common.yaml" in tail

    def test_truly_empty_block_before_another_top_key(self):
        # resources: with no entries at all, immediately followed by components:.
        text = "resources:\ncomponents:\n  - ../base/common.yaml\n"
        new, changed = kz.add_resource(text, "service.yaml")
        assert changed
        assert kz.list_resources(new) == ["service.yaml"]
        head, tail = new.split("components:")
        assert "- service.yaml" in head
        assert "- service.yaml" not in tail

    def test_creates_block_when_no_resources_key(self):
        # No `resources:` key at all — the block must be created, not orphaned.
        text = (
            "apiVersion: kustomize.config.k8s.io/v1beta1\n"
            "kind: Kustomization\n"
        )
        new, changed = kz.add_resource(text, "service.yaml")
        assert changed
        assert kz.has_resource(new, "service.yaml")
        assert kz.list_resources(new) == ["service.yaml"]
        assert "resources:\n  - service.yaml\n" in new
        # Existing top-level keys are preserved.
        assert "kind: Kustomization" in new

    def test_creates_block_when_no_trailing_newline(self):
        text = "kind: Kustomization"  # no resources:, no trailing newline
        new, changed = kz.add_resource(text, "service.yaml")
        assert changed
        assert kz.list_resources(new) == ["service.yaml"]
        assert new == "kind: Kustomization\nresources:\n  - service.yaml\n"
