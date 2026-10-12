"""ci-fetch-tools.py verifies before it extracts, and trusts a cached binary
only against the asset the pin names."""
from __future__ import annotations

import hashlib
import io
import os
import tarfile
import zipfile
from pathlib import Path

import pytest

from script_loader import load_script

gate = load_script("ci-fetch-tools.py", register=True)

PAYLOAD = b"#!/bin/sh\necho tool\n"
OTHER = b"#!/bin/sh\necho newer\n"
# A file outside the cache that no run may touch.
KEEP = b"keep me\n"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def as_url(path: Path) -> str:
    """A `file://` URL, so no test reaches the network."""
    return path.resolve().as_uri()


def write_raw(path: Path, data: bytes = PAYLOAD) -> Path:
    path.write_bytes(data)
    return path


def require_lzma() -> None:
    """Stop a tar.xz case on a build without lzma instead of skipping in CI."""
    try:
        import lzma  # noqa: F401
    except ImportError:
        if os.environ.get("CI"):
            pytest.fail("this Python has no lzma — the tar.xz case cannot run")
        pytest.skip("this Python has no lzma")


def require_dir_fd_rename() -> None:
    """Stop the descriptor case on a platform without renameat, not skip in CI."""
    if os.rename in os.supports_dir_fd:
        return
    if os.environ.get("CI"):
        pytest.fail("this platform has no renameat — the descriptor case cannot run")
    pytest.skip("this platform has no renameat")


def write_tar(path: Path, members: dict, mode: str = "w:gz") -> Path:
    with tarfile.open(path, mode) as bundle:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            bundle.addfile(info, io.BytesIO(data))
    return path


def write_zip(path: Path, members: dict) -> Path:
    with zipfile.ZipFile(path, "w") as bundle:
        for name, data in members.items():
            bundle.writestr(name, data)
    return path


def pin(monkeypatch, name: str, **fields) -> gate.Tool:
    """Replace one TOOLS entry for the duration of a test."""
    fields.setdefault("version", "9.9.9")
    tool = gate.Tool(**fields)
    monkeypatch.setitem(gate.TOOLS, name, tool)
    return tool


def run(monkeypatch, argv: list, directory: Path) -> int:
    """`main()` with no env override in play, so a stray export cannot leak in."""
    for key in list(os.environ):
        if key.startswith("TOOL_"):
            monkeypatch.delenv(key, raising=False)
    return gate.main(["--dir", str(directory), *argv])


class TestTheShippedTable:
    """The table is the contract a consumer runs; a typo in it is undetectable
    at the call site."""

    def test_the_table_is_not_empty(self):
        """Vacuity guard: an empty table would pass every case below."""
        assert set(gate.TOOLS) >= {
            "amtool", "jq", "kubeconform", "kustomize", "promtool", "shellcheck",
            "terraform",
        }

    @pytest.mark.parametrize("name", sorted(gate.TOOLS))
    def test_every_pin_is_a_full_sha256(self, name):
        sha256 = gate.TOOLS[name].sha256
        assert len(sha256) == 64 and set(sha256) <= set("0123456789abcdef"), sha256

    @pytest.mark.parametrize("name", sorted(gate.TOOLS))
    def test_every_url_and_member_renders_from_the_version_alone(self, name):
        """An unknown placeholder would only surface on a live fetch."""
        tool = gate.TOOLS[name]
        assert tool.url.format(version=tool.version).startswith("https://")
        assert tool.member.format(version=tool.version) or tool.kind == gate.RAW

    @pytest.mark.parametrize("name", sorted(gate.TOOLS))
    def test_every_archive_kind_is_one_the_extractor_handles(self, name):
        kind = gate.TOOLS[name].kind
        assert kind in {gate.RAW, "zip", *gate.ARCHIVE_MODES}

    @pytest.mark.parametrize("name", sorted(gate.TOOLS))
    def test_only_a_raw_pin_omits_its_member(self, name):
        tool = gate.TOOLS[name]
        assert bool(tool.member) == (tool.kind != gate.RAW)


class TestArchiveKinds:
    def test_a_raw_binary_is_installed_executable(self, monkeypatch, tmp_path):
        asset = write_raw(tmp_path / "jq-linux-amd64")
        pin(monkeypatch, "jq", url=as_url(asset), sha256=sha(PAYLOAD), kind=gate.RAW)
        target = tmp_path / "bin"
        assert run(monkeypatch, ["jq"], target) == 0
        assert (target / "jq").read_bytes() == PAYLOAD
        assert os.access(target / "jq", os.X_OK)

    def test_a_tar_gz_member_is_installed(self, monkeypatch, tmp_path):
        asset = write_tar(
            tmp_path / "prometheus.tar.gz",
            {"prometheus-9.9.9.linux-amd64/promtool": PAYLOAD, "LICENSE": b"x"},
        )
        pin(
            monkeypatch, "promtool", url=as_url(asset), sha256=sha(asset.read_bytes()),
            kind="tar.gz", member="prometheus-{version}.linux-amd64/promtool",
        )
        target = tmp_path / "bin"
        assert run(monkeypatch, ["promtool"], target) == 0
        assert (target / "promtool").read_bytes() == PAYLOAD

    def test_a_tar_xz_member_is_installed(self, monkeypatch, tmp_path):
        require_lzma()
        asset = write_tar(
            tmp_path / "shellcheck.tar.xz",
            {"shellcheck-v9.9.9/shellcheck": PAYLOAD},
            mode="w:xz",
        )
        pin(
            monkeypatch, "shellcheck", url=as_url(asset),
            sha256=sha(asset.read_bytes()), kind="tar.xz",
            member="shellcheck-v{version}/shellcheck",
        )
        target = tmp_path / "bin"
        assert run(monkeypatch, ["shellcheck"], target) == 0
        assert (target / "shellcheck").read_bytes() == PAYLOAD

    def test_a_zip_member_is_installed(self, monkeypatch, tmp_path):
        asset = write_zip(
            tmp_path / "terraform.zip",
            {"terraform": PAYLOAD, "LICENSE.txt": b"x"},
        )
        pin(
            monkeypatch, "terraform", url=as_url(asset),
            sha256=sha(asset.read_bytes()), kind="zip", member="terraform",
        )
        target = tmp_path / "bin"
        assert run(monkeypatch, ["terraform"], target) == 0
        assert (target / "terraform").read_bytes() == PAYLOAD
        assert os.access(target / "terraform", os.X_OK)

    def test_the_success_line_names_the_version_and_the_path(
        self, monkeypatch, tmp_path, capsys
    ):
        asset = write_raw(tmp_path / "jq-linux-amd64")
        pin(monkeypatch, "jq", url=as_url(asset), sha256=sha(PAYLOAD), kind=gate.RAW)
        target = tmp_path / "bin"
        run(monkeypatch, ["jq"], target)
        assert capsys.readouterr().out.strip() == "jq 9.9.9 -> %s" % (target / "jq")


class TestRefusals:
    def test_a_sha_mismatch_exits_2_and_leaves_nothing(
        self, monkeypatch, tmp_path, capsys
    ):
        asset = write_raw(tmp_path / "jq-linux-amd64")
        pin(monkeypatch, "jq", url=as_url(asset), sha256="0" * 64, kind=gate.RAW)
        target = tmp_path / "bin"
        assert run(monkeypatch, ["jq"], target) == 2
        assert "sha256 mismatch" in capsys.readouterr().err
        assert list(target.iterdir()) == []

    def test_an_unknown_tool_exits_2_listing_the_known_names(
        self, monkeypatch, tmp_path, capsys
    ):
        assert run(monkeypatch, ["kubectl"], tmp_path / "bin") == 2
        err = capsys.readouterr().err
        assert "kubectl" in err
        assert "kubeconform" in err and "terraform" in err

    def test_a_download_failure_exits_2_naming_the_url(
        self, monkeypatch, tmp_path, capsys
    ):
        missing = as_url(tmp_path / "absent" / "jq-linux-amd64")
        pin(monkeypatch, "jq", url=missing, sha256=sha(PAYLOAD), kind=gate.RAW)
        assert run(monkeypatch, ["jq"], tmp_path / "bin") == 2
        assert missing in capsys.readouterr().err

    def test_a_traversing_member_is_rejected(self, monkeypatch, tmp_path, capsys):
        """A `..` member must be refused rather than extracted."""
        asset = write_tar(tmp_path / "evil.tar.gz", {"../escaped": PAYLOAD})
        pin(
            monkeypatch, "kustomize", url=as_url(asset),
            sha256=sha(asset.read_bytes()), kind="tar.gz", member="../escaped",
        )
        target = tmp_path / "bin"
        assert run(monkeypatch, ["kustomize"], target) == 2
        assert "escapes the archive" in capsys.readouterr().err
        assert not (tmp_path / "escaped").exists()

    @pytest.mark.parametrize("member", ["/etc/passwd", "a/../../b", ".."])
    def test_safe_member_refuses_every_escaping_shape(self, member):
        with pytest.raises(gate.FetchError):
            gate.safe_member(member)

    def test_a_missing_member_exits_2(self, monkeypatch, tmp_path, capsys):
        asset = write_tar(tmp_path / "kustomize.tar.gz", {"LICENSE": b"x"})
        pin(
            monkeypatch, "kustomize", url=as_url(asset),
            sha256=sha(asset.read_bytes()), kind="tar.gz", member="kustomize",
        )
        assert run(monkeypatch, ["kustomize"], tmp_path / "bin") == 2
        assert "no member 'kustomize'" in capsys.readouterr().err

    def test_an_unreadable_archive_exits_2(self, monkeypatch, tmp_path, capsys):
        """A verified download that is not the archive the kind claims."""
        asset = write_raw(tmp_path / "terraform.zip", b"not a zip\n")
        pin(
            monkeypatch, "terraform", url=as_url(asset),
            sha256=sha(b"not a zip\n"), kind="zip", member="terraform",
        )
        target = tmp_path / "bin"
        assert run(monkeypatch, ["terraform"], target) == 2
        assert "cannot read" in capsys.readouterr().err
        assert list(target.iterdir()) == []

    def test_an_unknown_archive_kind_exits_2(self, monkeypatch, tmp_path, capsys):
        """The table is code, so a bad kind is an operator error, not a crash."""
        asset = write_raw(tmp_path / "blob")
        pin(
            monkeypatch, "jq", url=as_url(asset), sha256=sha(PAYLOAD),
            kind="tar.bz2", member="jq",
        )
        assert run(monkeypatch, ["jq"], tmp_path / "bin") == 2
        assert "unknown archive kind" in capsys.readouterr().err

    def test_naming_no_tool_exits_2(self, monkeypatch, tmp_path, capsys):
        assert run(monkeypatch, [], tmp_path / "bin") == 2
        assert "--list" in capsys.readouterr().err


class TestEnvOverrides:
    def test_a_version_override_without_a_sha_exits_2(
        self, monkeypatch, tmp_path, capsys
    ):
        asset = write_raw(tmp_path / "jq-linux-amd64")
        pin(monkeypatch, "jq", url=as_url(asset), sha256=sha(PAYLOAD), kind=gate.RAW)
        target = tmp_path / "bin"
        monkeypatch.setenv("TOOL_JQ_VERSION", "1.0.0")
        monkeypatch.delenv("TOOL_JQ_SHA256", raising=False)
        assert gate.main(["--dir", str(target), "jq"]) == 2
        assert "TOOL_JQ_SHA256" in capsys.readouterr().err
        assert list(target.iterdir()) == []

    def test_an_override_with_both_halves_fetches_the_overridden_version(
        self, monkeypatch, tmp_path
    ):
        write_raw(tmp_path / "jq-9.9.9", PAYLOAD)
        write_raw(tmp_path / "jq-1.0.0", OTHER)
        pin(
            monkeypatch, "jq", url=as_url(tmp_path) + "/jq-{version}",
            sha256=sha(PAYLOAD), kind=gate.RAW,
        )
        target = tmp_path / "bin"
        monkeypatch.setenv("TOOL_JQ_VERSION", "1.0.0")
        monkeypatch.setenv("TOOL_JQ_SHA256", sha(OTHER))
        assert gate.main(["--dir", str(target), "jq"]) == 0
        assert (target / "jq").read_bytes() == OTHER

    def test_an_override_for_another_tool_does_not_block_this_one(
        self, monkeypatch, tmp_path
    ):
        """Only the requested tools are resolved, so an unrelated half-made
        override cannot fail an unrelated fetch."""
        asset = write_raw(tmp_path / "jq-linux-amd64")
        pin(monkeypatch, "jq", url=as_url(asset), sha256=sha(PAYLOAD), kind=gate.RAW)
        monkeypatch.setenv("TOOL_TERRAFORM_VERSION", "1.0.0")
        monkeypatch.delenv("TOOL_TERRAFORM_SHA256", raising=False)
        assert gate.main(["--dir", str(tmp_path / "bin"), "jq"]) == 0

    def test_resolve_keeps_the_table_pin_when_nothing_is_set(self):
        tool = gate.TOOLS["jq"]
        assert gate.resolve("jq", tool, {}) == tool


class TestIdempotence:
    """The pin in the script decides, through the asset it names: nothing a
    mutable cache directory says about itself is trusted."""

    def test_a_matching_stamp_and_binary_is_a_no_op(self, monkeypatch, tmp_path, capsys):
        asset = write_raw(tmp_path / "jq-linux-amd64")
        pin(monkeypatch, "jq", url=as_url(asset), sha256=sha(PAYLOAD), kind=gate.RAW)
        target = tmp_path / "bin"
        assert run(monkeypatch, ["jq"], target) == 0
        capsys.readouterr()
        assert run(monkeypatch, ["jq"], target) == 0
        assert capsys.readouterr().out.strip() == "jq 9.9.9: present"

    def test_a_truncated_binary_re_fetches(self, monkeypatch, tmp_path):
        """An interrupted write or a half-restored cache: the bytes are not the
        ones the asset installs."""
        asset = write_raw(tmp_path / "jq-linux-amd64")
        pin(monkeypatch, "jq", url=as_url(asset), sha256=sha(PAYLOAD), kind=gate.RAW)
        target = tmp_path / "bin"
        assert run(monkeypatch, ["jq"], target) == 0
        (target / "jq").write_bytes(PAYLOAD[:5])
        assert run(monkeypatch, ["jq"], target) == 0
        assert (target / "jq").read_bytes() == PAYLOAD

    def test_a_tampered_binary_under_its_own_stamp_re_fetches(
        self, monkeypatch, tmp_path
    ):
        """What a stamp in the cache directory cannot catch: binary and stamp
        rewritten together. The asset the pin names can."""
        asset = write_raw(tmp_path / "jq-linux-amd64")
        pin(monkeypatch, "jq", url=as_url(asset), sha256=sha(PAYLOAD), kind=gate.RAW)
        target = tmp_path / "bin"
        assert run(monkeypatch, ["jq"], target) == 0
        (target / "jq").write_bytes(OTHER)
        gate.stamp_path(target, "jq").write_text(
            "9.9.9 %s %s\n" % (sha(PAYLOAD), sha(OTHER))
        )
        assert run(monkeypatch, ["jq"], target) == 0
        assert (target / "jq").read_bytes() == PAYLOAD

    def test_a_tampered_archive_member_is_re_extracted(self, monkeypatch, tmp_path):
        """For an archive pin the expected bytes come out of the kept asset,
        which outlives its source here."""
        archive = write_tar(tmp_path / "jq.tar.gz", {"jq-9.9.9/jq": PAYLOAD})
        pin(
            monkeypatch, "jq", url=as_url(archive), sha256=sha(archive.read_bytes()),
            kind="tar.gz", member="jq-{version}/jq",
        )
        target = tmp_path / "bin"
        assert run(monkeypatch, ["jq"], target) == 0
        (target / "jq").write_bytes(OTHER)
        gate.stamp_path(target, "jq").write_text(
            "9.9.9 %s %s\n" % (sha(archive.read_bytes()), sha(OTHER))
        )
        archive.unlink()
        assert run(monkeypatch, ["jq"], target) == 0
        assert (target / "jq").read_bytes() == PAYLOAD

    def test_a_tampered_cached_asset_re_fetches(self, monkeypatch, tmp_path):
        """Asset, binary and stamp all replaced at once: the asset is re-hashed
        against the pin, never trusted for being there."""
        asset = write_raw(tmp_path / "jq-linux-amd64")
        pin(monkeypatch, "jq", url=as_url(asset), sha256=sha(PAYLOAD), kind=gate.RAW)
        target = tmp_path / "bin"
        assert run(monkeypatch, ["jq"], target) == 0
        gate.asset_path(target, "jq").write_bytes(OTHER)
        (target / "jq").write_bytes(OTHER)
        gate.stamp_path(target, "jq").write_text(
            "9.9.9 %s %s\n" % (sha(PAYLOAD), sha(OTHER))
        )
        assert run(monkeypatch, ["jq"], target) == 0
        assert (target / "jq").read_bytes() == PAYLOAD
        assert gate.asset_path(target, "jq").read_bytes() == PAYLOAD

    def test_a_binary_whose_asset_is_gone_is_not_trusted(
        self, monkeypatch, tmp_path, capsys
    ):
        """A cache that kept only the binary cannot stand in for the asset: with
        the download unreachable the run fails rather than trusting it."""
        asset = write_raw(tmp_path / "jq-linux-amd64")
        pin(monkeypatch, "jq", url=as_url(asset), sha256=sha(PAYLOAD), kind=gate.RAW)
        target = tmp_path / "bin"
        assert run(monkeypatch, ["jq"], target) == 0
        gate.asset_path(target, "jq").unlink()
        asset.unlink()
        assert run(monkeypatch, ["jq"], target) == 2
        assert "failed to download" in capsys.readouterr().err

    def test_a_verified_cache_needs_no_download(self, monkeypatch, tmp_path, capsys):
        """The kept asset makes the second run offline, and it still prints the
        skip rather than re-installing."""
        asset = write_raw(tmp_path / "jq-linux-amd64")
        pin(monkeypatch, "jq", url=as_url(asset), sha256=sha(PAYLOAD), kind=gate.RAW)
        target = tmp_path / "bin"
        assert run(monkeypatch, ["jq"], target) == 0
        asset.unlink()
        capsys.readouterr()
        assert run(monkeypatch, ["jq"], target) == 0
        assert capsys.readouterr().out.strip() == "jq 9.9.9: present"

    def test_the_verified_asset_is_kept_beside_the_binaries(
        self, monkeypatch, tmp_path
    ):
        """The archive itself is what the cache has to carry, under `.assets`."""
        archive = write_tar(tmp_path / "jq.tar.gz", {"jq-9.9.9/jq": PAYLOAD})
        pin(
            monkeypatch, "jq", url=as_url(archive), sha256=sha(archive.read_bytes()),
            kind="tar.gz", member="jq-{version}/jq",
        )
        target = tmp_path / "bin"
        assert run(monkeypatch, ["jq"], target) == 0
        kept = gate.asset_path(target, "jq")
        assert kept == target / ".assets" / "jq"
        assert kept.read_bytes() == archive.read_bytes()

    def test_a_directory_where_the_binary_belongs_re_fetches(
        self, monkeypatch, tmp_path
    ):
        """A cached path that is not a regular file reads as absent, instead of
        failing the rename at the end of the install."""
        asset = write_raw(tmp_path / "jq-linux-amd64")
        pin(monkeypatch, "jq", url=as_url(asset), sha256=sha(PAYLOAD), kind=gate.RAW)
        target = tmp_path / "bin"
        (target / "jq" / "nested").mkdir(parents=True)
        assert run(monkeypatch, ["jq"], target) == 0
        assert (target / "jq").read_bytes() == PAYLOAD

    def test_a_symlinked_binary_re_fetches(self, monkeypatch, tmp_path):
        """A symlink is not the file the pin installs, whatever it points at. The
        install replaces the link itself, leaving what it pointed at alone."""
        asset = write_raw(tmp_path / "jq-linux-amd64")
        pin(monkeypatch, "jq", url=as_url(asset), sha256=sha(PAYLOAD), kind=gate.RAW)
        target = tmp_path / "bin"
        assert run(monkeypatch, ["jq"], target) == 0
        elsewhere = write_raw(tmp_path / "elsewhere", OTHER)
        elsewhere.chmod(0o755)
        (target / "jq").unlink()
        (target / "jq").symlink_to(elsewhere)
        assert run(monkeypatch, ["jq"], target) == 0
        assert not (target / "jq").is_symlink()
        assert (target / "jq").read_bytes() == PAYLOAD
        assert elsewhere.read_bytes() == OTHER

    def test_a_non_executable_binary_re_fetches(self, monkeypatch, tmp_path):
        """Right bytes, no execute bit: the job would fail on the call, so the
        install runs again rather than reporting it present."""
        asset = write_raw(tmp_path / "jq-linux-amd64")
        pin(monkeypatch, "jq", url=as_url(asset), sha256=sha(PAYLOAD), kind=gate.RAW)
        target = tmp_path / "bin"
        assert run(monkeypatch, ["jq"], target) == 0
        (target / "jq").chmod(0o644)
        assert run(monkeypatch, ["jq"], target) == 0
        assert os.access(target / "jq", os.X_OK)

    def test_the_stamp_records_the_extracted_binary_not_the_archive(
        self, monkeypatch, tmp_path
    ):
        """The archive digest cannot speak for the member pulled out of it, so
        both are recorded."""
        archive = write_tar(tmp_path / "jq.tar.gz", {"jq-9.9.9/jq": PAYLOAD})
        pin(
            monkeypatch, "jq", url=as_url(archive), sha256=sha(archive.read_bytes()),
            kind="tar.gz", member="jq-{version}/jq",
        )
        target = tmp_path / "bin"
        assert run(monkeypatch, ["jq"], target) == 0
        stamp = gate.stamp_path(target, "jq").read_text().split()
        assert stamp == ["9.9.9", sha(archive.read_bytes()), sha(PAYLOAD)]
        assert run(monkeypatch, ["jq"], target) == 0

    def test_a_missing_stamp_re_fetches(self, monkeypatch, tmp_path):
        """A pre-seeded, cached or truncated binary claims nothing about itself."""
        asset = write_raw(tmp_path / "jq-linux-amd64")
        pin(monkeypatch, "jq", url=as_url(asset), sha256=sha(PAYLOAD), kind=gate.RAW)
        target = tmp_path / "bin"
        target.mkdir()
        (target / "jq").write_bytes(b"stale\n")
        assert run(monkeypatch, ["jq"], target) == 0
        assert (target / "jq").read_bytes() == PAYLOAD

    def test_a_stale_stamp_re_fetches(self, monkeypatch, tmp_path):
        """The bump that silently did not take effect: the cached .bin survived
        with the old version in it."""
        asset = write_raw(tmp_path / "jq-linux-amd64")
        pin(monkeypatch, "jq", url=as_url(asset), sha256=sha(PAYLOAD), kind=gate.RAW)
        target = tmp_path / "bin"
        target.mkdir()
        (target / "jq").write_bytes(b"stale\n")
        gate.stamp_path(target, "jq").write_text(
            "9.9.8 %s %s\n" % (sha(PAYLOAD), sha(b"stale\n"))
        )
        assert run(monkeypatch, ["jq"], target) == 0
        assert (target / "jq").read_bytes() == PAYLOAD
        assert gate.stamp_path(target, "jq").read_text() == "9.9.9 %s %s\n" % (
            sha(PAYLOAD), sha(PAYLOAD),
        )

    def test_a_stamp_at_another_checksum_re_fetches(self, monkeypatch, tmp_path):
        """Same version, re-cut asset: the sha256 half of the stamp catches it."""
        asset = write_raw(tmp_path / "jq-linux-amd64")
        pin(monkeypatch, "jq", url=as_url(asset), sha256=sha(PAYLOAD), kind=gate.RAW)
        target = tmp_path / "bin"
        target.mkdir()
        (target / "jq").write_bytes(b"stale\n")
        gate.stamp_path(target, "jq").write_text(
            "9.9.9 %s %s\n" % (sha(OTHER), sha(b"stale\n"))
        )
        assert run(monkeypatch, ["jq"], target) == 0
        assert (target / "jq").read_bytes() == PAYLOAD

    def test_an_env_override_re_fetches_over_a_table_stamp(self, monkeypatch, tmp_path):
        """The override is part of the resolved tool, so the stamp tracks it."""
        write_raw(tmp_path / "jq-9.9.9", PAYLOAD)
        write_raw(tmp_path / "jq-1.0.0", OTHER)
        pin(
            monkeypatch, "jq", url=as_url(tmp_path) + "/jq-{version}",
            sha256=sha(PAYLOAD), kind=gate.RAW,
        )
        target = tmp_path / "bin"
        assert run(monkeypatch, ["jq"], target) == 0
        monkeypatch.setenv("TOOL_JQ_VERSION", "1.0.0")
        monkeypatch.setenv("TOOL_JQ_SHA256", sha(OTHER))
        assert gate.main(["--dir", str(target), "jq"]) == 0
        assert (target / "jq").read_bytes() == OTHER

    def test_force_replaces_a_stamped_install(self, monkeypatch, tmp_path):
        asset = write_raw(tmp_path / "jq-linux-amd64")
        pin(monkeypatch, "jq", url=as_url(asset), sha256=sha(PAYLOAD), kind=gate.RAW)
        target = tmp_path / "bin"
        assert run(monkeypatch, ["jq"], target) == 0
        (target / "jq").write_bytes(b"stale\n")
        assert run(monkeypatch, ["--force", "jq"], target) == 0
        assert (target / "jq").read_bytes() == PAYLOAD

    def test_a_failed_install_leaves_no_stamp(self, monkeypatch, tmp_path, capsys):
        """An interrupted install must read as absent, not as the pinned version."""
        asset = write_raw(tmp_path / "jq-linux-amd64", OTHER)
        pin(monkeypatch, "jq", url=as_url(asset), sha256=sha(PAYLOAD), kind=gate.RAW)
        target = tmp_path / "bin"
        assert run(monkeypatch, ["jq"], target) == 2
        assert not gate.stamp_path(target, "jq").exists()
        assert "sha256 mismatch" in capsys.readouterr().err


class TestCacheEscapes:
    """Every write lands inside the install directory: a link restored into the
    cache is refused rather than followed out of it."""

    def test_an_assets_dir_linked_outside_is_refused(
        self, monkeypatch, tmp_path, capsys
    ):
        """The kept asset would land on whatever the link names."""
        asset = write_raw(tmp_path / "jq-linux-amd64")
        pin(monkeypatch, "jq", url=as_url(asset), sha256=sha(PAYLOAD), kind=gate.RAW)
        outside = tmp_path / "outside"
        outside.mkdir()
        planted = write_raw(outside / "jq", KEEP)
        target = tmp_path / "bin"
        target.mkdir()
        (target / gate.ASSETS_DIR).symlink_to(outside)
        assert run(monkeypatch, ["jq"], target) == 2
        assert "outside the cache" in capsys.readouterr().err
        assert planted.read_bytes() == KEEP
        assert not (target / "jq").exists()

    def test_a_linked_cache_entry_is_refused(self, monkeypatch, tmp_path, capsys):
        """A link where the asset belongs is not the cache's own file, so the
        asset is neither read through it nor replaced under it."""
        asset = write_raw(tmp_path / "jq-linux-amd64")
        pin(monkeypatch, "jq", url=as_url(asset), sha256=sha(PAYLOAD), kind=gate.RAW)
        planted = write_raw(tmp_path / "planted", KEEP)
        target = tmp_path / "bin"
        (target / gate.ASSETS_DIR).mkdir(parents=True)
        gate.asset_path(target, "jq").symlink_to(planted)
        assert run(monkeypatch, ["jq"], target) == 2
        assert "refusing the symlink" in capsys.readouterr().err
        assert planted.read_bytes() == KEEP
        assert not (target / "jq").exists()

    def test_a_stamp_link_swapped_in_after_the_check_is_replaced_not_followed(
        self, monkeypatch, tmp_path
    ):
        """The stamp goes through the same descriptor-based replace as the
        binary, so a link planted between the check and the write is replaced."""
        asset = write_raw(tmp_path / "jq-linux-amd64")
        pin(monkeypatch, "jq", url=as_url(asset), sha256=sha(PAYLOAD), kind=gate.RAW)
        planted = write_raw(tmp_path / "planted", KEEP)
        target = tmp_path / "bin"
        target.mkdir()
        stamp = gate.stamp_path(target, "jq")
        original = gate.replace_into

        def plant_after_the_binary(directory, source, name):
            # The binary lands first; the link appears before the stamp write.
            result = original(directory, source, name)
            if name == "jq" and not stamp.exists():
                stamp.symlink_to(planted)
            return result

        monkeypatch.setattr(gate, "replace_into", plant_after_the_binary)
        assert run(monkeypatch, ["jq"], target) == 0
        assert planted.read_bytes() == KEEP
        assert not stamp.is_symlink() and stamp.is_file()

    def test_no_directory_descriptor_rename_fails_closed(self, monkeypatch, tmp_path, capsys):
        """Without a descriptor-relative rename the containment cannot be held,
        so the write is refused rather than made through a path."""
        asset = write_raw(tmp_path / "jq-linux-amd64")
        pin(monkeypatch, "jq", url=as_url(asset), sha256=sha(PAYLOAD), kind=gate.RAW)
        target = tmp_path / "bin"
        target.mkdir()
        monkeypatch.setattr(gate.os, "supports_dir_fd", set())
        assert run(monkeypatch, ["jq"], target) == 2
        assert "directory descriptor" in capsys.readouterr().err
        assert not (target / "jq").exists()

    def test_a_linked_stamp_is_refused(self, monkeypatch, tmp_path, capsys):
        """The stamp is written by path, so a link there would be followed."""
        asset = write_raw(tmp_path / "jq-linux-amd64")
        pin(monkeypatch, "jq", url=as_url(asset), sha256=sha(PAYLOAD), kind=gate.RAW)
        planted = write_raw(tmp_path / "planted", KEEP)
        target = tmp_path / "bin"
        target.mkdir()
        gate.stamp_path(target, "jq").symlink_to(planted)
        assert run(monkeypatch, ["jq"], target) == 2
        assert "refusing the symlink" in capsys.readouterr().err
        assert planted.read_bytes() == KEEP
        assert not (target / "jq").exists()

    def test_a_clean_cache_installs_keeps_and_skips(self, monkeypatch, tmp_path):
        """The guards are invisible to a cache the script owns: the asset is a
        real file beside the binary, and the second run reads it offline."""
        asset = write_raw(tmp_path / "jq-linux-amd64")
        pin(monkeypatch, "jq", url=as_url(asset), sha256=sha(PAYLOAD), kind=gate.RAW)
        target = tmp_path / "bin"
        assert run(monkeypatch, ["jq"], target) == 0
        kept = gate.asset_path(target, "jq")
        assert not kept.is_symlink() and kept.read_bytes() == PAYLOAD
        assert (target / "jq").read_bytes() == PAYLOAD
        asset.unlink()
        assert run(monkeypatch, ["jq"], target) == 0

    def test_a_symlinked_install_dir_is_still_installed_into(
        self, monkeypatch, tmp_path
    ):
        """`--dir` is the caller's own path: resolving it is what makes a link
        inside it visible, and a link to it stays usable."""
        asset = write_raw(tmp_path / "jq-linux-amd64")
        pin(monkeypatch, "jq", url=as_url(asset), sha256=sha(PAYLOAD), kind=gate.RAW)
        real = tmp_path / "real-bin"
        real.mkdir()
        linked = tmp_path / "bin"
        linked.symlink_to(real)
        assert run(monkeypatch, ["jq"], linked) == 0
        assert (real / "jq").read_bytes() == PAYLOAD

    def test_the_descriptor_refuses_a_link_swapped_in_behind_the_check(
        self, monkeypatch, tmp_path, capsys
    ):
        """The window between the check and the rename: with the check stubbed
        out, the descriptor on the directory itself is what refuses."""
        require_dir_fd_rename()
        asset = write_raw(tmp_path / "jq-linux-amd64")
        pin(monkeypatch, "jq", url=as_url(asset), sha256=sha(PAYLOAD), kind=gate.RAW)
        outside = tmp_path / "outside"
        outside.mkdir()
        planted = write_raw(outside / "jq", KEEP)
        target = tmp_path / "bin"
        target.mkdir()
        (target / gate.ASSETS_DIR).symlink_to(outside)
        monkeypatch.setattr(gate, "in_cache", lambda directory, path: path)
        assert run(monkeypatch, ["jq"], target) == 2
        assert capsys.readouterr().err.startswith("ERROR:")
        assert planted.read_bytes() == KEEP

    def test_the_default_comes_from_ci_project_dir(self, tmp_path):
        assert gate.default_dir({"CI_PROJECT_DIR": str(tmp_path)}) == tmp_path / ".bin"

    def test_the_default_is_a_local_bin_off_ci(self):
        assert gate.default_dir({}) == Path(".bin")

    def test_a_run_without_dir_installs_under_ci_project_dir(
        self, monkeypatch, tmp_path
    ):
        asset = write_raw(tmp_path / "jq-linux-amd64")
        pin(monkeypatch, "jq", url=as_url(asset), sha256=sha(PAYLOAD), kind=gate.RAW)
        monkeypatch.setenv("CI_PROJECT_DIR", str(tmp_path / "project"))
        assert gate.main(["jq"]) == 0
        assert (tmp_path / "project" / ".bin" / "jq").read_bytes() == PAYLOAD


class TestListing:
    def test_list_prints_every_pin_and_exits_zero(self, capsys):
        assert gate.main(["--list"]) == 0
        out = capsys.readouterr().out
        assert out.splitlines()[0].split() == ["TOOL", "VERSION", "SHA256"]
        for name, tool in gate.TOOLS.items():
            assert "%s " % name in out
            assert tool.version in out
            assert tool.sha256[:16] in out

    def test_list_reflects_an_override(self, monkeypatch):
        table = gate.pin_table(env={"TOOL_JQ_VERSION": "1.0.0",
                                    "TOOL_JQ_SHA256": "f" * 64})
        assert "jq           1.0.0      ffffffffffffffff" in table

    def test_list_needs_no_tool_argument(self, capsys):
        assert gate.main(["--list"]) == 0
        assert capsys.readouterr().err == ""


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
