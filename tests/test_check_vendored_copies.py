"""Tests for scripts/check-vendored-copies.py, the consumer-manifest engine.

The engine and this suite live in the library ONLY: a consumer runs the gate
from a library checkout via `--lib-path` and ships nothing but its own
manifest plus a smoke test that the manifest passes at its pinned ref.
Vendoring the engine is deliberately impossible — the offer list excludes it
(tests/test_vendorable_paths.py::test_the_engine_itself_is_not_offered), so a
manifest naming it fails the membership arm.
"""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent

_SPEC = importlib.util.spec_from_file_location(
    "check_vendored_copies", REPO / "scripts" / "check-vendored-copies.py"
)
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)  # type: ignore[union-attr]


@pytest.fixture()
def world(tmp_path):
    """A fake library checkout + consumer repo carrying a minimal manifest."""
    lib = tmp_path / "lib"
    (lib / "scripts").mkdir(parents=True)
    (lib / "lint").mkdir()
    (lib / "scripts" / "tool.py").write_text("shared\n")
    (lib / "lint" / "ruff.toml").write_text("profile\n")

    consumer = tmp_path / "consumer"
    (consumer / "scripts").mkdir(parents=True)
    (consumer / "scripts" / "tool.py").write_text("shared\n")
    (consumer / "ruff.toml").write_text("profile-local\n")

    # The engine requires a working tree that ships the engine to ship the
    # offer list beside it, so the fixture carries one covering every path the
    # tests may name; individual tests overwrite it to probe the membership arm.
    (lib / "scripts" / "vendorable-paths.yml").write_text(
        yaml.safe_dump(
            {"vendorable": [
                "scripts/tool.py", "lint/ruff.toml",
                "scripts/added-later.py", "lint/editorconfig",
            ]}
        )
    )

    manifest = consumer / "scripts" / "vendored-manifest.yml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "vendored": ["scripts/tool.py"],
                "forked": [
                    {
                        "lib": "lint/ruff.toml",
                        "consumer": "ruff.toml",
                        "reason": "narrower target set",
                        "reconciled_sha256": mod._sha(b"profile\n"),
                    }
                ],
            }
        )
    )
    return lib, consumer, manifest


def _offer(lib: Path, paths: list[str]) -> None:
    (lib / "scripts" / "vendorable-paths.yml").write_text(
        yaml.safe_dump({"vendorable": paths})
    )


def _seed_git(lib: Path) -> None:
    """Commit the fake library checkout so `--ref HEAD` resolves."""
    subprocess.run(["git", "-C", str(lib), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(lib), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(lib), "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-qm", "seed"],
        check=True,
    )


def _run(world, extra=()):
    lib, consumer, _manifest = world
    return mod.main(
        [
            "--repo-root",
            str(consumer),
            "--lib-path",
            str(lib),
            *extra,
        ]
    )


class TestVendored:
    def test_identical_copies_pass(self, world):
        assert _run(world) == 0

    def test_drift_fails(self, world, capsys):
        _, consumer, _ = world
        (consumer / "scripts" / "tool.py").write_text("edited locally\n")
        assert _run(world) == 1
        assert "drifted" in capsys.readouterr().err

    def test_missing_local_copy_fails(self, world, capsys):
        _, consumer, _ = world
        (consumer / "scripts" / "tool.py").unlink()
        assert _run(world) == 1
        assert "missing here" in capsys.readouterr().err

    def test_library_dropping_the_file_fails(self, world, capsys):
        lib, _, _ = world
        (lib / "scripts" / "tool.py").unlink()
        assert _run(world) == 1
        assert "no longer ships" in capsys.readouterr().err


class TestForked:
    def test_a_converged_fork_fails(self, world, capsys):
        _, consumer, _ = world
        (consumer / "ruff.toml").write_text("profile\n")
        assert _run(world) == 1
        assert "move the entry to `vendored`" in capsys.readouterr().err

    def test_an_upstream_change_since_the_last_reconcile_fails(self, world, capsys):
        lib, _, _ = world
        (lib / "lint" / "ruff.toml").write_text("profile v2\n")
        assert _run(world) == 1
        err = capsys.readouterr().err
        assert "since this fork was last reconciled" in err
        assert mod._sha(b"profile v2\n") in err

    def test_no_reconciled_sha_only_asserts_divergence(self, world):
        lib, _, manifest = world
        doc = yaml.safe_load(manifest.read_text())
        del doc["forked"][0]["reconciled_sha256"]
        manifest.write_text(yaml.safe_dump(doc))
        (lib / "lint" / "ruff.toml").write_text("profile v2\n")
        assert _run(world) == 0

    def test_a_reasonless_fork_entry_is_an_operator_error(self, world, capsys):
        _, _, manifest = world
        doc = yaml.safe_load(manifest.read_text())
        del doc["forked"][0]["reason"]
        manifest.write_text(yaml.safe_dump(doc))
        assert _run(world) == 2
        assert "no `reason:`" in capsys.readouterr().err


class TestOffer:
    """The offer-membership arm: lib knows WHAT it exports, never who copies it."""

    def test_manifest_within_the_offer_passes(self, world):
        lib, _, _ = world
        _offer(lib, ["scripts/tool.py", "lint/ruff.toml"])
        assert _run(world) == 0

    def test_an_unoffered_lib_path_fails(self, world, capsys):
        lib, _, _ = world
        _offer(lib, ["scripts/tool.py"])  # the fork's lint/ruff.toml is not offered
        assert _run(world) == 1
        assert "not in the library's" in capsys.readouterr().err

    def test_a_ref_predating_the_offer_list_skips_the_arm(self, world, capsys):
        """History is not failed retroactively: at a pin cut before the offer
        list existed, the membership arm skips — and says so — while the
        byte-identity arms still run."""
        lib, _, _ = world
        (lib / "scripts" / "vendorable-paths.yml").unlink()
        _seed_git(lib)  # committed WITHOUT an offer list
        _offer(lib, [])  # working tree regains one afterwards, deliberately empty
        assert _run(world, ["--ref", "HEAD"]) == 0
        assert "predates the offer list" in capsys.readouterr().err

    def test_a_missing_working_tree_offer_list_is_an_operator_error(self, world, capsys):
        """No ref means the compare target is the working tree, and a tree that
        ships the engine ships the offer list — absence is a broken checkout,
        not history, and must not silently disable the membership arm."""
        lib, _, _ = world
        (lib / "scripts" / "vendorable-paths.yml").unlink()
        assert _run(world) == 2
        assert "missing from the library working tree" in capsys.readouterr().err

    def test_a_malformed_offer_list_is_an_operator_error(self, world, capsys):
        """Every malformed shape reports cleanly — a top-level list or scalar
        must not surface as an AttributeError traceback."""
        lib, _, _ = world
        for malformed in ("vendorable: not-a-list\n", "- a\n- list\n", "scalar\n"):
            (lib / "scripts" / "vendorable-paths.yml").write_text(malformed)
            assert _run(world) == 2
            assert "needs a `vendorable:` list" in capsys.readouterr().err


class TestCli:
    def test_a_missing_manifest_is_an_operator_error(self, world, capsys):
        _, consumer, manifest = world
        manifest.unlink()
        assert _run(world) == 2
        assert "vendored-manifest.yml" in capsys.readouterr().err

    def test_an_empty_manifest_gates_nothing_and_says_so(self, world, capsys):
        _, _, manifest = world
        manifest.write_text("{}\n")
        assert _run(world) == 2
        assert "gates nothing" in capsys.readouterr().err

    def test_explicit_manifest_path_overrides_the_convention(self, world, tmp_path):
        lib, consumer, manifest = world
        moved = tmp_path / "elsewhere.yml"
        moved.write_text(manifest.read_text())
        manifest.unlink()
        assert _run(world, ["--manifest", str(moved)]) == 0

    def test_missing_lib_checkout_never_skips(self, world, tmp_path, capsys):
        _, consumer, _ = world
        with pytest.raises(SystemExit) as excinfo:
            mod.main(["--repo-root", str(consumer),
                      "--lib-path", str(tmp_path / "absent")])
        # 2, not 1: a misconfigured gate must not read as a drift finding.
        assert excinfo.value.code == 2
        assert "never skips" in capsys.readouterr().err

    def test_list_prints_both_kinds(self, world, capsys):
        assert _run(world, ["--list"]) == 0
        out = capsys.readouterr().out
        assert "vendored\tscripts/tool.py" in out
        assert "forked\truff.toml" in out

    def test_ref_reads_the_blob_at_that_ref(self, world, capsys):
        lib, _, _ = world
        _seed_git(lib)
        (lib / "scripts" / "tool.py").write_text("worktree only\n")
        # The committed blob still matches the consumer's copy.
        assert _run(world, ["--ref", "HEAD"]) == 0
        # An unresolvable ref falls back to the working tree, which has drifted —
        # and the run says which tree it compared against.
        capsys.readouterr()
        assert _run(world, ["--ref", "v9.9.9"]) == 1
        assert "does not resolve" in capsys.readouterr().err

    def test_a_path_added_after_a_resolving_ref_is_not_shipped_by_it(self, world, capsys):
        """The fallback is per REF, not per PATH.

        A file the library adds AFTER the pinned tag is not in that release, so
        comparing the consumer's copy against the newer working tree would pass a
        copy the pin cannot deliver. The message must name THAT direction: the
        adoption-window fix is bumping the pin, the opposite of the "no longer
        ships" fix.
        """
        lib, consumer, manifest = world
        _seed_git(lib)
        (lib / "scripts" / "added-later.py").write_text("post-tag\n")
        (consumer / "scripts" / "added-later.py").write_text("post-tag\n")
        doc = yaml.safe_load(manifest.read_text())
        doc["vendored"].append("scripts/added-later.py")
        manifest.write_text(yaml.safe_dump(doc))

        assert _run(world, ["--ref", "HEAD"]) == 1
        err = capsys.readouterr().err
        assert "does not carry it" in err and "Bump the pin" in err
        assert "no longer ships" not in err
        # Without a ref the same pair is byte-identical and passes.
        assert _run(world) == 0

    def test_a_fork_added_after_a_resolving_ref_gets_the_same_direction(self, world, capsys):
        """The fork arm reported the same wrong direction, and a fork entry is
        the harder one to un-delete: `reason:` and `reconciled_sha256` go with
        it."""
        lib, consumer, manifest = world
        _seed_git(lib)
        (lib / "lint" / "editorconfig").write_text("shared\n")
        (consumer / ".editorconfig").write_text("local\n")
        doc = yaml.safe_load(manifest.read_text())
        doc["forked"].append(
            {"lib": "lint/editorconfig", "consumer": ".editorconfig", "reason": "per-repo"}
        )
        manifest.write_text(yaml.safe_dump(doc))

        assert _run(world, ["--ref", "HEAD"]) == 1
        err = capsys.readouterr().err
        assert "does not carry it" in err and "no longer ships" not in err

    def test_a_path_the_library_really_dropped_still_says_so(self, world, capsys):
        """The other direction keeps its own wording: gone from the working tree
        AND from the ref means the entry (or the copy) is what has to go."""
        lib, _, _ = world
        _seed_git(lib)
        (lib / "scripts" / "tool.py").unlink()
        subprocess.run(["git", "-C", str(lib), "rm", "-q", "scripts/tool.py"], check=True)
        subprocess.run(
            ["git", "-C", str(lib), "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-qm", "drop"],
            check=True,
        )
        assert _run(world, ["--ref", "HEAD"]) == 1
        assert "no longer ships scripts/tool.py" in capsys.readouterr().err


class TestManifestSafety:
    """The manifest gates the repo it lives in — nothing outside it."""

    def test_a_misspelled_section_key_is_an_operator_error(self, world, capsys):
        _, _, manifest = world
        doc = yaml.safe_load(manifest.read_text())
        doc["vendoerd"] = doc.pop("vendored")
        manifest.write_text(yaml.safe_dump(doc))
        assert _run(world) == 2
        assert "unknown keys: vendoerd" in capsys.readouterr().err

    def test_a_non_list_section_is_an_operator_error(self, world, capsys):
        _, _, manifest = world
        doc = yaml.safe_load(manifest.read_text())
        doc["vendored"] = {"scripts/tool.py": True}
        manifest.write_text(yaml.safe_dump(doc))
        assert _run(world) == 2
        assert "needs `vendored:` to be a list" in capsys.readouterr().err

    def test_a_non_mapping_manifest_is_an_operator_error(self, world, capsys):
        _, _, manifest = world
        manifest.write_text("- just\n- a\n- list\n")
        assert _run(world) == 2
        assert "must contain a mapping" in capsys.readouterr().err

    def test_a_dotdot_path_is_rejected_at_parse(self, world, capsys):
        _, _, manifest = world
        doc = yaml.safe_load(manifest.read_text())
        doc["vendored"].append({"lib": "scripts/tool.py", "consumer": "../outside.py"})
        manifest.write_text(yaml.safe_dump(doc))
        assert _run(world) == 2
        assert "canonical repo-relative path" in capsys.readouterr().err

    def test_an_absolute_path_is_rejected_at_parse(self, world, capsys):
        _, _, manifest = world
        doc = yaml.safe_load(manifest.read_text())
        doc["vendored"].append({"lib": "scripts/tool.py", "consumer": "/etc/passwd"})
        manifest.write_text(yaml.safe_dump(doc))
        assert _run(world) == 2
        assert "canonical repo-relative path" in capsys.readouterr().err

    def test_an_escaping_symlink_is_a_finding(self, world, tmp_path, capsys):
        """Parse-clean but resolving outside the repo: the symlink variant is
        caught at check time and reported as a problem, not certified."""
        lib, consumer, manifest = world
        outside = tmp_path / "outside.py"
        outside.write_text("shared\n")
        (consumer / "scripts" / "link.py").symlink_to(outside)
        (lib / "scripts" / "link.py").write_text("shared\n")
        doc = yaml.safe_load(manifest.read_text())
        doc["vendored"].append({"lib": "scripts/tool.py", "consumer": "scripts/link.py"})
        manifest.write_text(yaml.safe_dump(doc))
        _offer(lib, ["scripts/tool.py", "lint/ruff.toml"])
        assert _run(world) == 1
        assert "is a symlink" in capsys.readouterr().err

    def test_a_duplicate_consumer_destination_is_an_operator_error(self, world, capsys):
        """Two entries writing one destination describe an ambiguous copy
        relationship — both could pass while the bytes match either upstream."""
        _, _, manifest = world
        doc = yaml.safe_load(manifest.read_text())
        doc["vendored"].append({"lib": "lint/ruff.toml", "consumer": "scripts/tool.py"})
        manifest.write_text(yaml.safe_dump(doc))
        assert _run(world) == 2
        assert "more than once" in capsys.readouterr().err

    def test_an_escaping_offer_path_is_an_operator_error(self, world, capsys):
        _, _, _ = world
        lib = world[0]
        _offer(lib, ["../outside.py"])
        assert _run(world) == 2
        assert "canonical repo-relative path" in capsys.readouterr().err

    def test_a_duplicate_yaml_key_is_an_operator_error(self, world, capsys):
        """PyYAML's default keeps the LAST duplicate mapping key — a manifest
        with two `vendored:` sections would silently drop every entry in the
        first, ungating declared copies with no visible signal."""
        _, _, manifest = world
        manifest.write_text(
            "vendored:\n  - scripts/tool.py\nvendored:\n  - lint/ruff.toml\n"
        )
        assert _run(world) == 2
        assert "duplicate mapping key" in capsys.readouterr().err

    def test_an_unknown_entry_key_is_an_operator_error(self, world, capsys):
        """A typo like reconciled_sha265 would silently disarm the guard it
        meant to arm."""
        _, _, manifest = world
        doc = yaml.safe_load(manifest.read_text())
        doc["forked"][0]["reconciled_sha265"] = doc["forked"][0].pop("reconciled_sha256")
        manifest.write_text(yaml.safe_dump(doc))
        assert _run(world) == 2
        assert "unknown keys: reconciled_sha265" in capsys.readouterr().err

    def test_a_string_form_forked_entry_is_an_operator_error(self, world, capsys):
        """The short form cannot carry the mandatory reason."""
        _, _, manifest = world
        doc = yaml.safe_load(manifest.read_text())
        doc["forked"].append("lint/editorconfig")
        manifest.write_text(yaml.safe_dump(doc))
        assert _run(world) == 2
        assert "must be a mapping with a `reason:`" in capsys.readouterr().err

    def test_an_unhashable_yaml_key_is_an_operator_error(self, world, capsys):
        _, _, manifest = world
        manifest.write_text("? [a, list, key]\n: x\nvendored:\n  - scripts/tool.py\n")
        assert _run(world) == 2
        assert "unhashable mapping key" in capsys.readouterr().err

    def test_a_release_shipping_the_engine_without_the_offer_is_broken(self, world, capsys):
        """A resolving ref is only HISTORY when its engine predates the offer
        list; an engine that names the file without shipping it is a broken
        release, and skipping would certify unoffered paths."""
        lib, _, _ = world
        (lib / "scripts" / "vendorable-paths.yml").unlink()
        (lib / "scripts" / "check-vendored-copies.py").write_text(
            "# fake engine that reads scripts/vendorable-paths.yml\n"
        )
        _seed_git(lib)
        _offer(lib, ["scripts/tool.py", "lint/ruff.toml"])  # working tree fine
        assert _run(world, ["--ref", "HEAD"]) == 2
        assert "broken release" in capsys.readouterr().err

    def test_a_non_canonical_path_alias_is_an_operator_error(self, world, capsys):
        """pathlib collapses `.` segments, so `scripts/./tool.py` would count
        as a second destination and dodge the duplicate check."""
        _, _, manifest = world
        for alias in ("scripts/./tool.py", "scripts//tool.py", "scripts/tool.py\x00"):
            doc = yaml.safe_load(manifest.read_text())
            doc["vendored"] = [{"lib": "scripts/tool.py", "consumer": alias}]
            manifest.write_text(yaml.safe_dump(doc))
            assert _run(world) == 2, f"alias {alias!r} was accepted"
            assert "canonical repo-relative path" in capsys.readouterr().err


    def test_an_in_repo_symlink_is_a_finding_too(self, world, capsys):
        """Even inside the repo, read_bytes() follows the link while git
        stores the target text — the certified bytes are not the committed
        artifact."""
        lib, consumer, manifest = world
        (consumer / "scripts" / "real.py").write_text("shared\n")
        (consumer / "scripts" / "alias.py").symlink_to(consumer / "scripts" / "real.py")
        (lib / "scripts" / "alias.py").write_text("shared\n")
        doc = yaml.safe_load(manifest.read_text())
        doc["vendored"].append({"lib": "scripts/tool.py", "consumer": "scripts/alias.py"})
        manifest.write_text(yaml.safe_dump(doc))
        _offer(lib, ["scripts/tool.py", "lint/ruff.toml"])
        assert _run(world) == 1
        assert "is a symlink" in capsys.readouterr().err

    def test_a_library_side_symlink_is_a_finding_in_working_tree_compare(self, world, capsys):
        """The mirror of the consumer-side rule: a pre-tag compare through a
        library symlink certifies bytes the pinned ref will not serve."""
        lib, consumer, manifest = world
        (lib / "scripts" / "real2.py").write_text("shared\n")
        (lib / "scripts" / "linked.py").symlink_to(lib / "scripts" / "real2.py")
        (consumer / "scripts" / "linked.py").write_text("shared\n")
        doc = yaml.safe_load(manifest.read_text())
        doc["vendored"].append("scripts/linked.py")
        manifest.write_text(yaml.safe_dump(doc))
        _offer(lib, ["scripts/tool.py", "lint/ruff.toml", "scripts/linked.py"])
        assert _run(world) == 1
        assert "library-side" in capsys.readouterr().err

    def test_a_symlinked_offer_file_is_an_operator_error(self, world, capsys):
        lib, _, _ = world
        real = lib / "scripts" / "offer-real.yml"
        real.write_text((lib / "scripts" / "vendorable-paths.yml").read_text())
        (lib / "scripts" / "vendorable-paths.yml").unlink()
        (lib / "scripts" / "vendorable-paths.yml").symlink_to(real)
        assert _run(world) == 2
        assert "is a symlink in the library working tree" in capsys.readouterr().err

    def test_a_listed_but_unservable_blob_is_an_operator_error(self, world, capsys):
        """git failing to SERVE a blob must not read as the release not
        shipping it — that would silently disable the arm that asked."""
        lib, _, _ = world
        _seed_git(lib)
        # Corrupt the object store: the tree lists scripts/tool.py but the
        # blob behind it is gone.
        import subprocess as sp
        blob = sp.run(["git", "-C", str(lib), "rev-parse", "HEAD:scripts/tool.py"],
                      capture_output=True, text=True).stdout.strip()
        victim = lib / ".git" / "objects" / blob[:2] / blob[2:]
        victim.unlink()
        assert _run(world, ["--ref", "HEAD"]) == 2
        err = capsys.readouterr().err
        assert "git show could not serve it" in err or "failing repository" in err

    def test_a_duplicate_offer_key_is_an_operator_error(self, world, capsys):
        lib, _, _ = world
        (lib / "scripts" / "vendorable-paths.yml").write_text(
            "vendorable:\n  - scripts/tool.py\nvendorable:\n  - lint/ruff.toml\n"
        )
        assert _run(world) == 2
        assert "duplicate mapping key" in capsys.readouterr().err

    def test_list_needs_no_library_checkout(self, world, capsys, tmp_path):
        """--list prints the parsed manifest and nothing else — it must not
        demand the checkout the compare arms need."""
        _, consumer, _ = world
        rc = mod.main(["--repo-root", str(consumer), "--list",
                       "--lib-path", str(tmp_path / "definitely-absent")])
        assert rc == 0
        assert "vendored\tscripts/tool.py" in capsys.readouterr().out

    def test_a_committed_library_symlink_at_a_ref_is_a_named_finding(self, world, capsys):
        """At a resolving ref git show serves the link's target TEXT — the
        mismatch would surface as a misleading "drifted", sending the reader
        to vendor the link text; it gets its own finding instead."""
        lib, consumer, manifest = world
        (lib / "scripts" / "real3.py").write_text("shared\n")
        (lib / "scripts" / "reflink.py").symlink_to("real3.py")
        _seed_git(lib)
        # Working tree cleans up: the link is replaced by a real file, so only
        # the REF carries the symlink.
        (lib / "scripts" / "reflink.py").unlink()
        (lib / "scripts" / "reflink.py").write_text("shared\n")
        (consumer / "scripts" / "reflink.py").write_text("shared\n")
        doc = yaml.safe_load(manifest.read_text())
        doc["vendored"].append("scripts/reflink.py")
        manifest.write_text(yaml.safe_dump(doc))
        _offer(lib, ["scripts/tool.py", "lint/ruff.toml", "scripts/reflink.py"])
        assert _run(world, ["--ref", "HEAD"]) == 1
        assert "committed symlink" in capsys.readouterr().err
