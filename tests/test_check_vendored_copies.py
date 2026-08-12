"""Tests for scripts/check-vendored-copies.py and the registry it reads.

CANONICAL SUITE. A consumer that vendors the script vendors this file too and
adds only its own smoke test — that its own registry entry passes.
"""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
REGISTRY = REPO / "scripts" / "vendored-paths.yml"

_SPEC = importlib.util.spec_from_file_location(
    "check_vendored_copies", REPO / "scripts" / "check-vendored-copies.py"
)
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)  # type: ignore[union-attr]


@pytest.fixture()
def world(tmp_path):
    """A fake library checkout + consumer repo wired to a minimal registry."""
    lib = tmp_path / "lib"
    (lib / "scripts").mkdir(parents=True)
    (lib / "lint").mkdir()
    (lib / "scripts" / "tool.py").write_text("shared\n")
    (lib / "lint" / "ruff.toml").write_text("profile\n")

    consumer = tmp_path / "consumer"
    (consumer / "scripts").mkdir(parents=True)
    (consumer / "scripts" / "tool.py").write_text("shared\n")
    (consumer / "ruff.toml").write_text("profile-local\n")

    registry = tmp_path / "registry.yml"
    registry.write_text(
        yaml.safe_dump(
            {
                "consumers": {
                    "demo": {
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
                }
            }
        )
    )
    return lib, consumer, registry


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
    lib, consumer, registry = world
    return mod.main(
        [
            "--consumer",
            "demo",
            "--repo-root",
            str(consumer),
            "--lib-path",
            str(lib),
            "--registry",
            str(registry),
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
        lib, _, registry = world
        doc = yaml.safe_load(registry.read_text())
        del doc["consumers"]["demo"]["forked"][0]["reconciled_sha256"]
        registry.write_text(yaml.safe_dump(doc))
        (lib / "lint" / "ruff.toml").write_text("profile v2\n")
        assert _run(world) == 0

    def test_a_reasonless_fork_entry_is_an_operator_error(self, world, capsys):
        _, _, registry = world
        doc = yaml.safe_load(registry.read_text())
        del doc["consumers"]["demo"]["forked"][0]["reason"]
        registry.write_text(yaml.safe_dump(doc))
        assert _run(world) == 2
        assert "no `reason:`" in capsys.readouterr().err


class TestCli:
    def test_unknown_consumer_is_an_operator_error(self, world, capsys):
        lib, consumer, registry = world
        rc = mod.main(
            ["--consumer", "nope", "--repo-root", str(consumer), "--lib-path", str(lib),
             "--registry", str(registry)]
        )
        assert rc == 2
        assert "known: demo" in capsys.readouterr().err

    def test_missing_lib_checkout_never_skips(self, tmp_path, capsys):
        with pytest.raises(SystemExit) as excinfo:
            mod.main(["--consumer", "demo", "--repo-root", str(tmp_path),
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
        copy the pin cannot deliver.
        """
        lib, consumer, registry = world
        _seed_git(lib)
        (lib / "scripts" / "added-later.py").write_text("post-tag\n")
        (consumer / "scripts" / "added-later.py").write_text("post-tag\n")
        doc = yaml.safe_load(registry.read_text())
        doc["consumers"]["demo"]["vendored"].append("scripts/added-later.py")
        registry.write_text(yaml.safe_dump(doc))

        assert _run(world, ["--ref", "HEAD"]) == 1
        assert "no longer ships scripts/added-later.py" in capsys.readouterr().err
        # Without a ref the same pair is byte-identical and passes.
        assert _run(world) == 0


class TestShippedRegistry:
    """The registry the library actually ships stays well-formed."""

    def test_every_consumer_parses(self):
        consumers = yaml.safe_load(REGISTRY.read_text())["consumers"]
        assert consumers
        for name in consumers:
            vendored, forked = mod.load_registry(REGISTRY, name)
            assert vendored or forked

    def test_every_registered_library_path_exists(self):
        consumers = yaml.safe_load(REGISTRY.read_text())["consumers"]
        for name in consumers:
            for entry in [*mod.load_registry(REGISTRY, name)[0], *mod.load_registry(REGISTRY, name)[1]]:
                assert (REPO / entry.lib).is_file(), f"{name}: {entry.lib} is not in the library"

    def test_every_reconciled_sha_matches_the_shipped_file(self):
        """`reconciled_sha256` is read at the consumer's PINNED TAG, so it must
        be the sha of the file as this release ships it.

        A release that edits a forked file and leaves the old sha behind reds
        every consumer's gate on day one, and no consumer-side edit can clear it:
        the value lives here. Re-take it before tagging.
        """
        for name in yaml.safe_load(REGISTRY.read_text())["consumers"]:
            _vendored, forked = mod.load_registry(REGISTRY, name)
            for entry in forked:
                if not entry.reconciled:
                    continue
                actual = mod._sha((REPO / entry.lib).read_bytes())
                assert entry.reconciled == actual, (
                    f"{name}: reconciled_sha256 for {entry.lib} is stale — "
                    f"set it to {actual}"
                )

    def test_coverage_reaches_beyond_scripts(self):
        """Lint profiles and workflows are registered, not only scripts/."""
        registered = set()
        for name in yaml.safe_load(REGISTRY.read_text())["consumers"]:
            vendored, forked = mod.load_registry(REGISTRY, name)
            registered.update(e.lib for e in [*vendored, *forked])
        assert any(p.startswith("lint/") for p in registered)
        assert any(p.startswith("tests/") for p in registered)
        assert any(p.startswith("ci/") for p in registered)
