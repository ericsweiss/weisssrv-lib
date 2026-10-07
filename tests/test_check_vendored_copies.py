"""Tests for scripts/check-vendored-copies.py, the consumer-manifest engine.

The engine and this suite live in the library only; a consumer runs the gate
via `--lib-path` and ships its own manifest. The offer list excludes the engine.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

from script_loader import load_script

mod = load_script("check-vendored-copies.py")


@pytest.fixture()
def world(tmp_path):
    """A fake library checkout + consumer repo carrying a minimal manifest."""
    lib = tmp_path / "lib"
    (lib / "scripts").mkdir(parents=True)
    (lib / "lint").mkdir()
    # resolve_lib_root validates any --lib-path by this marker.
    (lib / "scripts" / "check-vendored-copies.py").write_text("# engine\n")
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


# The developer's ~/.gitconfig is neutralised: a global commit.gpgsign, an
# init.templateDir hook or a global pre-commit hook would error every git-backed
# test here for reasons unrelated to the engine.
GIT_ISOLATED = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run git in `repo` with the ambient git configuration neutralised."""
    commit = args and args[0] == "commit"
    argv = ["git", "-C", str(repo)]
    if commit:
        argv += ["-c", "commit.gpgsign=false"]
    argv += list(args)
    if commit:
        argv += ["--no-verify"]
    return subprocess.run(
        argv, check=check, capture_output=True, text=True,
        env={**os.environ, **GIT_ISOLATED},
    )


def _seed_git(lib: Path) -> None:
    """Commit the fake library checkout so `--ref HEAD` resolves."""
    git(lib, "init", "-q")
    git(lib, "add", "-A")
    git(lib, "commit", "-qm", "seed")


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


class TestProseOnlyFork:
    """A fork that converged except for reworded comments keeps a `reason:` that
    describes nothing: the gate must say so, and `comment_only: true` declares
    the header difference that is deliberate."""

    def _fork(self, world, lib_text: str, local_text: str, **extra) -> None:
        lib, consumer, manifest = world
        (lib / "scripts" / "tool.py").write_text(lib_text)
        (consumer / "scripts" / "tool.py").write_text(local_text)
        entry = {"lib": "scripts/tool.py", "reason": "narrower target set"}
        entry.update(extra)
        manifest.write_text(yaml.safe_dump({"forked": [entry]}))

    def test_a_comment_only_fork_fails(self, world, capsys):
        self._fork(world, "# upstream wording\nshared\n", "# local wording\nshared\n")
        assert _run(world) == 1
        err = capsys.readouterr().err
        assert "only in comments and blank lines" in err
        assert "narrower target set" in err

    def test_a_blank_line_only_fork_fails(self, world, capsys):
        self._fork(world, "shared\n", "\nshared\n\n")
        assert _run(world) == 1
        assert "only in comments and blank lines" in capsys.readouterr().err

    def test_declaring_comment_only_passes(self, world):
        self._fork(
            world,
            "# upstream wording\nshared\n",
            "# local wording\nshared\n",
            comment_only=True,
        )
        assert _run(world) == 0

    def test_a_code_divergence_still_passes_undeclared(self, world):
        """Mutation guard: the new arm must not fire on a real fork."""
        self._fork(world, "# same\nshared\n", "# same\nshared-local\n")
        assert _run(world) == 0

    def test_declaring_comment_only_on_a_code_fork_fails(self, world, capsys):
        self._fork(
            world, "# same\nshared\n", "# same\nshared-local\n", comment_only=True
        )
        assert _run(world) == 1
        assert "diverges from scripts/tool.py in code" in capsys.readouterr().err

    def test_a_changed_shebang_is_code_not_a_comment(self, world):
        self._fork(world, "#!/bin/sh\nshared\n", "#!/usr/bin/env bash\nshared\n")
        assert _run(world) == 0

    def test_an_unknown_suffix_is_never_called_prose_only(self, world):
        """No markers for the suffix means no claim: the fork passes as before."""
        lib, consumer, manifest = world
        (lib / "scripts" / "blob.bin").write_text("# upstream\nshared\n")
        (consumer / "scripts" / "blob.bin").write_text("# local\nshared\n")
        _offer(lib, ["scripts/blob.bin"])
        manifest.write_text(
            yaml.safe_dump(
                {"forked": [{"lib": "scripts/blob.bin", "reason": "site data"}]}
            )
        )
        assert _run(world) == 0

    def test_a_non_boolean_comment_only_is_an_operator_error(self, world, capsys):
        self._fork(world, "shared\n", "# x\nshared\n", comment_only="yes")
        assert _run(world) == 2
        assert "non-boolean `comment_only:`" in capsys.readouterr().err

    def test_comment_only_is_rejected_on_a_vendored_entry(self, world, capsys):
        _, _, manifest = world
        manifest.write_text(
            yaml.safe_dump(
                {"vendored": [{"lib": "scripts/tool.py", "comment_only": True}]}
            )
        )
        assert _run(world) == 2
        assert "unknown keys" in capsys.readouterr().err


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

    def test_a_clean_run_without_a_ref_is_not_reported_as_a_verified_pass(self, world, capsys):
        assert _run(world) == 0
        assert "REF UNVERIFIED" in capsys.readouterr().out

    def test_require_ref_refuses_an_unresolvable_ref(self, world, capsys):
        lib, _, _ = world
        _seed_git(lib)
        assert _run(world, ["--ref", "HEAD", "--require-ref"]) == 0
        capsys.readouterr()
        # 2, not 1: an unverifiable comparison is a misconfigured gate.
        assert _run(world, ["--ref", "v9.9.9", "--require-ref"]) == 2
        assert "proves nothing about the pinned release" in capsys.readouterr().err

    def test_require_ref_refuses_a_run_with_no_ref_at_all(self, world, capsys):
        assert _run(world, ["--require-ref"]) == 2
        assert "no --ref given" in capsys.readouterr().err

    def test_a_path_added_after_a_resolving_ref_is_not_shipped_by_it(self, world, capsys):
        """A path added after the pinned ref fails with 'Bump the pin', not
        'no longer ships'."""
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
        """The fork arm reports the same direction as the vendored arm."""
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
        """Gone from the working tree AND the ref keeps the 'no longer ships'
        wording: the entry or the copy is what has to go."""
        lib, _, _ = world
        _seed_git(lib)
        (lib / "scripts" / "tool.py").unlink()
        git(lib, "rm", "-q", "scripts/tool.py")
        git(lib, "commit", "-qm", "drop")
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
        lib, _, _ = world
        _offer(lib, ["../outside.py"])
        assert _run(world) == 2
        assert "canonical repo-relative path" in capsys.readouterr().err

    def test_a_duplicate_yaml_key_is_an_operator_error(self, world, capsys):
        """A duplicate `vendored:` key is an operator error: PyYAML keeps only
        the last, silently ungating every entry in the first."""
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
        """A release that names the engine without shipping the offer list is
        an error, not history: skipping would certify unoffered paths."""
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
        """An in-repo symlink is a finding too: read_bytes() follows the link
        while git stores the target text."""
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
        """A blob git cannot serve is an operator error, not a release that
        does not ship the path."""
        lib, _, _ = world
        _seed_git(lib)
        # Corrupt the object store: the tree lists scripts/tool.py but the
        # blob behind it is gone.
        blob = git(lib, "rev-parse", "HEAD:scripts/tool.py").stdout.strip()
        victim = lib / ".git" / "objects" / blob[:2] / blob[2:]
        victim.unlink()
        assert _run(world, ["--ref", "HEAD"]) == 2
        err = capsys.readouterr().err
        assert "git show could not serve it" in err

    def test_a_duplicate_offer_key_is_an_operator_error(self, world, capsys):
        lib, _, _ = world
        (lib / "scripts" / "vendorable-paths.yml").write_text(
            "vendorable:\n  - scripts/tool.py\nvendorable:\n  - lint/ruff.toml\n"
        )
        assert _run(world) == 2
        assert "duplicate mapping key" in capsys.readouterr().err

    def test_a_lib_path_that_is_not_a_library_checkout_is_an_operator_error(
        self, world, capsys, tmp_path
    ):
        """A wrong --lib-path must not read back as every entry having drifted."""
        _, consumer, _ = world
        wrong = tmp_path / "not-the-library"
        wrong.mkdir()
        with pytest.raises(SystemExit) as excinfo:
            mod.main(["--repo-root", str(consumer), "--lib-path", str(wrong)])
        assert excinfo.value.code == 2
        assert "no weisssrv-lib checkout found" in capsys.readouterr().err

    def test_list_needs_no_library_checkout(self, world, capsys, tmp_path):
        """--list prints the parsed manifest without demanding a checkout."""
        _, consumer, _ = world
        rc = mod.main(["--repo-root", str(consumer), "--list",
                       "--lib-path", str(tmp_path / "definitely-absent")])
        assert rc == 0
        assert "vendored\tscripts/tool.py" in capsys.readouterr().out

    def test_a_committed_library_symlink_at_a_ref_is_a_named_finding(self, world, capsys):
        """A committed library symlink at a ref is its own finding: git show
        serves the link's target text, which would read as drift."""
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


class TestUnregisteredTwinScan:
    """The per-path arms judge only what the manifest declares, so a subtree
    nobody registered is invisible without --scan."""

    def _molecule_twin(self, world, relpath: str = "prepare-common.yml") -> Path:
        lib, consumer, _manifest = world
        shared = lib / "ansible_collections" / "weisssrv" / "infra" / "molecule-shared"
        shared.mkdir(parents=True, exist_ok=True)
        (shared / relpath).parent.mkdir(parents=True, exist_ok=True)
        (shared / relpath).write_text("scaffolding\n")
        _offer(
            lib,
            [
                "scripts/tool.py",
                "lint/ruff.toml",
                f"ansible_collections/weisssrv/infra/molecule-shared/{relpath}",
            ],
        )
        local = consumer / "ansible" / "molecule" / relpath
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text("scaffolding\n")
        return local

    SCAN = (
        "--scan",
        "ansible/molecule=ansible_collections/weisssrv/infra/molecule-shared",
    )

    def test_unregistered_twin_fails(self, world, capsys):
        self._molecule_twin(world)
        assert _run(world, self.SCAN) == 1
        err = capsys.readouterr().err
        assert "ansible/molecule/prepare-common.yml" in err
        assert "is in no manifest entry" in err

    def test_the_same_tree_passes_without_the_scan(self, world):
        """Without --scan the copy is invisible, which is the gap being closed."""
        self._molecule_twin(world)
        assert _run(world) == 0

    def test_registered_twin_passes(self, world):
        lib, consumer, manifest = world
        self._molecule_twin(world)
        doc = yaml.safe_load(manifest.read_text())
        doc["vendored"].append(
            {
                "lib": "ansible_collections/weisssrv/infra/molecule-shared/"
                       "prepare-common.yml",
                "consumer": "ansible/molecule/prepare-common.yml",
            }
        )
        manifest.write_text(yaml.safe_dump(doc))
        assert _run(world, self.SCAN) == 0

    def test_nested_twin_is_found(self, world, capsys):
        self._molecule_twin(world, "tasks/prepare-base.yml")
        assert _run(world, self.SCAN) == 1
        assert "ansible/molecule/tasks/prepare-base.yml" in capsys.readouterr().err

    def test_file_with_no_offered_twin_is_left_alone(self, world):
        lib, consumer, _manifest = world
        local = consumer / "ansible" / "molecule" / "site-only.yml"
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text("site data\n")
        assert _run(world, self.SCAN) == 0

    def test_missing_scan_dir_exits_2(self, world, capsys):
        assert _run(world, self.SCAN) == 2
        assert "which is not a directory" in capsys.readouterr().err

    def test_malformed_scan_argument_exits_2(self, world, capsys):
        assert _run(world, ["--scan", "ansible/molecule"]) == 2
        assert "CONSUMER_DIR=LIB_PREFIX" in capsys.readouterr().err
