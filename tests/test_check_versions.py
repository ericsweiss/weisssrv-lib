#!/usr/bin/env python3
"""Unit tests for scripts/check-versions.py.

Covers the comparison and APT engine, the mocked fetchers, the cache, the CLI
and the consumer-config layer, against tests/fixtures/version-registry/.
"""

import contextlib
import gzip
import http.client
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from script_loader import SCRIPTS, load_script

REPO = Path(__file__).resolve().parent.parent
script_path = SCRIPTS / "check-versions.py"
check_versions = load_script("check-versions.py", register=True)

FIXTURE_DIR = REPO / "tests" / "fixtures" / "version-registry"
FIXTURE_CONFIG = FIXTURE_DIR / "registry.py"
FIXTURE_REPO = FIXTURE_DIR / "repo"

parse_version_tuple = check_versions.parse_version_tuple
version_greater = check_versions.version_greater
read_pinned_image_versions = check_versions.read_pinned_image_versions
update_version_in_file = check_versions.update_version_in_file


@pytest.fixture(autouse=True, scope="module")
def _fixture_config():
    """Load the fixture consumer config, and undo the env write afterwards.

    main() re-resolves the config on every invocation, so the in-process CLI
    tests need it on the environment too.
    """
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("CHECK_VERSIONS_CONFIG", str(FIXTURE_CONFIG))
    check_versions.load_config(FIXTURE_CONFIG, repo_root=FIXTURE_REPO)
    yield
    monkeypatch.undo()


@pytest.fixture(autouse=True)
def _cache_dir_is_disposable(tmp_path_factory, monkeypatch):
    """No test writes a lookup cache into the fixture tree.

    main() reloads the config, which re-points CACHE_DIR, so the reload is
    wrapped to point it back at the disposable directory.
    """
    cache_dir = tmp_path_factory.mktemp("version-cache")
    real_load = check_versions.load_config

    def load_then_point(*args, **kwargs):
        cfg = real_load(*args, **kwargs)
        check_versions.CACHE_DIR = cache_dir
        return cfg

    original = check_versions.CACHE_DIR
    check_versions.CACHE_DIR = cache_dir
    monkeypatch.setattr(check_versions, "load_config", load_then_point)
    yield
    check_versions.CACHE_DIR = original


def mock_response(body: bytes):
    """A urlopen-style response usable as a context manager."""
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def write_tmp_vars(content: str) -> Path:
    """A throwaway vars file; the caller unlinks it."""
    handle = tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False)
    handle.write(content)
    handle.close()
    return Path(handle.name)


class TestCategoryFilterFailsClosed:
    """An unknown --category is an error, never an empty clean report."""

    def test_unknown_category_raises(self):
        with pytest.raises(ValueError, match="no services match category"):
            check_versions.check_all(
                services=[{"category": "github", "name": "x", "var_name": "x_version"}],
                category_filter="githubb",
            )

    def test_known_category_still_selects(self, monkeypatch):
        monkeypatch.setattr(check_versions, "read_current_versions", lambda: {})
        monkeypatch.setattr(
            check_versions, "check_service",
            lambda svc, cur, use_cache=True: check_versions.ServiceVersion(
                name="x", category="manual", current_version="1",
                var_name="x_version"))
        out = check_versions.check_all(
            services=[{"category": "manual", "name": "x", "var_name": "x_version"}],
            category_filter="manual")
        assert len(out) == 1


class TestTagRegexWithoutCaptureGroup:
    """A tag_regex without a capture group selects the whole tag."""

    def _pages(self, *tags):
        return {"results": [{"name": t} for t in tags], "next": None}

    def test_groupless_regex_selects_the_newest_tag(self, monkeypatch):
        monkeypatch.setattr(check_versions, "_make_request", lambda url: self._pages(
            "1.2.0", "1.10.0", "1.9.0", "latest"))
        assert check_versions._dockerhub_best_tag("x/y", r"^\d+\.\d+\.\d+$") == "1.10.0"

    def test_capture_group_regex_still_returns_the_captured_version(self, monkeypatch):
        monkeypatch.setattr(check_versions, "_make_request", lambda url: self._pages(
            "v1.2.0", "v1.10.0"))
        assert check_versions._dockerhub_best_tag(
            "x/y", r"^v(\d+\.\d+\.\d+)$") == "1.10.0"


class TestDockerHubTagWindow:
    """Docker Hub lists tags by last push, so a window can end before the pin."""

    def _pages(self, *pages):
        pages = list(pages)
        def fake(url):
            tags = pages.pop(0)
            return {"results": [{"name": t} for t in tags],
                    "next": "next-page" if pages else None}
        return fake

    def test_a_window_that_missed_the_pin_is_an_error_naming_the_fix(self, monkeypatch):
        monkeypatch.setattr(check_versions, "_make_request",
                            self._pages(["8.8.3-alpine", "8.6.7-alpine"]))
        svc = {"docker_image": "library/redis",
               "tag_regex": r"^(\d+\.\d+\.\d+-alpine)$",
               "_current_version": "8.10.1-alpine"}
        with pytest.raises(RuntimeError) as excinfo:
            check_versions.fetch_dockerhub_version(svc)
        message = str(excinfo.value)
        assert "window ended before the pinned tag" in message
        assert "8.8.3-alpine" in message and "8.10.1-alpine" in message
        assert "dockerhub_name_filter" in message

    def test_a_window_holding_a_newer_tag_reports_it(self, monkeypatch):
        monkeypatch.setattr(check_versions, "_make_request",
                            self._pages(["8.8.3-alpine", "8.10.2-alpine"]))
        svc = {"docker_image": "library/redis",
               "tag_regex": r"^(\d+\.\d+\.\d+-alpine)$",
               "_current_version": "8.10.1-alpine"}
        assert check_versions.fetch_dockerhub_version(svc) == "8.10.2-alpine"

    def test_the_pinned_tag_itself_is_not_a_miss(self, monkeypatch):
        monkeypatch.setattr(check_versions, "_make_request",
                            self._pages(["8.10.1-alpine", "8.8.3-alpine"]))
        svc = {"docker_image": "library/redis",
               "tag_regex": r"^(\d+\.\d+\.\d+-alpine)$",
               "_current_version": "8.10.1-alpine"}
        assert check_versions.fetch_dockerhub_version(svc) == "8.10.1-alpine"

    @pytest.mark.parametrize("current", ["unknown", "latest", ""])
    def test_a_pin_that_is_not_a_version_is_not_compared(self, monkeypatch, current):
        monkeypatch.setattr(check_versions, "_make_request",
                            self._pages(["1.2.0"]))
        svc = {"docker_image": "x/y", "_current_version": current}
        assert check_versions.fetch_dockerhub_version(svc) == "1.2.0"

    def test_a_prefixed_tag_scheme_is_compared_on_the_captured_version(self, monkeypatch):
        monkeypatch.setattr(check_versions, "_make_request",
                            self._pages(["release-1.2.0"]))
        svc = {"docker_image": "x/y", "tag_regex": r"^release-(\d+\.\d+\.\d+)$",
               "_current_version": "release-1.3.0"}
        with pytest.raises(RuntimeError, match="window ended before the pinned tag"):
            check_versions.fetch_dockerhub_version(svc)

    def test_a_pin_the_regex_does_not_read_is_not_compared(self, monkeypatch):
        monkeypatch.setattr(check_versions, "_make_request",
                            self._pages(["1.2.0"]))
        svc = {"docker_image": "x/y", "tag_regex": r"^(\d+\.\d+\.\d+)$",
               "_current_version": "9.9.9-alpine"}
        assert check_versions.fetch_dockerhub_version(svc) == "1.2.0"

    def test_a_pin_outside_the_version_prefix_names_the_prefix(self, monkeypatch):
        monkeypatch.setattr(check_versions, "_make_request",
                            self._pages(["1.15.9", "1.16.0"]))
        svc = {"docker_image": "x/y", "version_prefix": "1.15.",
               "_current_version": "1.16.0"}
        with pytest.raises(RuntimeError, match="outside version_prefix '1.15.'"):
            check_versions.fetch_dockerhub_version(svc)

    def test_dockerhub_max_pages_widens_the_window(self, monkeypatch):
        monkeypatch.setattr(check_versions, "_make_request",
                            self._pages(["8.8.3-alpine"], ["8.10.1-alpine"]))
        svc = {"docker_image": "library/redis",
               "tag_regex": r"^(\d+\.\d+\.\d+-alpine)$",
               "_current_version": "8.10.1-alpine",
               "dockerhub_max_pages": 2}
        assert check_versions.fetch_dockerhub_version(svc) == "8.10.1-alpine"

    def test_the_default_window_is_one_page(self, monkeypatch):
        calls = []
        def fake(url):
            calls.append(url)
            return {"results": [{"name": "1.0.0"}], "next": "more"}
        monkeypatch.setattr(check_versions, "_make_request", fake)
        check_versions.fetch_dockerhub_version({"docker_image": "x/y"})
        assert len(calls) == 1


class TestLsioTagWindow:
    """An LSIO entry gets the Docker Hub window check; its pin is the captured version."""

    SVC = {"docker_image": "linuxserver/sonarr",
           "lsio_version_regex": r"^version-(\d+\.\d+\.\d+\.\d+)$",
           "_current_version": "4.0.16.2944"}

    def _pages(self, *tags):
        return lambda url: {"results": [{"name": t} for t in tags], "next": None}

    def test_a_window_that_missed_the_pin_raises_naming_the_lsio_knobs(self, monkeypatch):
        monkeypatch.setattr(check_versions, "_make_request",
                            self._pages("version-4.0.15.2941", "latest"))
        with pytest.raises(RuntimeError) as excinfo:
            check_versions.fetch_lsio_version(dict(self.SVC))
        message = str(excinfo.value)
        assert "window ended before the pinned tag" in message
        assert "version-4.0.15.2941" in message and "4.0.16.2944" in message
        assert "lsio_name_filter" in message and "lsio_max_pages" in message
        assert "dockerhub_" not in message

    def test_a_window_holding_a_newer_tag_reports_it(self, monkeypatch):
        monkeypatch.setattr(check_versions, "_make_request",
                            self._pages("version-4.0.15.2941", "version-4.0.17.2950"))
        assert check_versions.fetch_lsio_version(dict(self.SVC)) == "4.0.17.2950"

    def test_the_pinned_version_itself_is_not_a_miss(self, monkeypatch):
        monkeypatch.setattr(check_versions, "_make_request",
                            self._pages("version-4.0.16.2944"))
        assert check_versions.fetch_lsio_version(dict(self.SVC)) == "4.0.16.2944"

    def test_a_bare_tag_scheme_is_checked_too(self, monkeypatch):
        monkeypatch.setattr(check_versions, "_make_request", self._pages("5.1.3"))
        svc = {"docker_image": "linuxserver/qbittorrent",
               "lsio_version_regex": r"^(\d+\.\d+\.\d+)$",
               "_current_version": "5.1.4"}
        with pytest.raises(RuntimeError, match="lsio_name_filter"):
            check_versions.fetch_lsio_version(svc)

    def test_a_bare_tag_scheme_reports_the_highest_tag(self, monkeypatch):
        monkeypatch.setattr(check_versions, "_make_request", self._pages("5.1.3", "5.1.4", "latest"))
        svc = {"docker_image": "linuxserver/qbittorrent",
               "lsio_version_regex": r"^(\d+\.\d+\.\d+)$"}
        assert check_versions.fetch_lsio_version(svc) == "5.1.4"

    @pytest.mark.parametrize("current", ["unknown", "latest", ""])
    def test_a_pin_that_is_not_a_version_is_not_compared(self, monkeypatch, current):
        monkeypatch.setattr(check_versions, "_make_request",
                            self._pages("version-4.0.15.2941"))
        svc = dict(self.SVC, _current_version=current)
        assert check_versions.fetch_lsio_version(svc) == "4.0.15.2941"


class TestPinnedImageVersionTracking(unittest.TestCase):
    """Digest-locked image pins that live outside the vars file."""

    def test_reads_aliased_ci_pin(self):
        versions = read_pinned_image_versions()
        self.assertEqual(versions["pr_agent_version"], "0.34")

    def test_reads_multi_manifest_pin(self):
        versions = read_pinned_image_versions()
        self.assertEqual(versions["python_cronjob_version"], "3.13-slim")

    def test_image_ref_override_is_what_matches_the_manifest(self):
        """The manifests spell `python:`, the API lookup name is
        `library/python`: without image_ref the pin resolves to nothing."""
        without_ref = [
            {k: v for k, v in svc.items() if k != "image_ref"}
            for svc in check_versions.SERVICE_REGISTRY
        ]
        with patch.object(check_versions, "SERVICE_REGISTRY", without_ref):
            self.assertNotIn("python_cronjob_version", read_pinned_image_versions())

    def test_divergent_multifile_pin_is_flagged(self):
        # Two manifests that must share one tag have drifted: raise rather than
        # silently selecting one (that would let CI go green on divergent pins).
        try:
            check_versions.load_config(
                FIXTURE_DIR / "registry-divergent.py", repo_root=FIXTURE_REPO
            )
            with self.assertRaises(RuntimeError) as ctx:
                read_pinned_image_versions()
        finally:
            check_versions.load_config(FIXTURE_CONFIG, repo_root=FIXTURE_REPO)
        self.assertIn("python_cronjob_version", str(ctx.exception))
        self.assertIn("diverge", str(ctx.exception))

    def test_unreadable_version_file_is_skipped(self):
        # A registry entry whose file is absent must not crash the whole read.
        with patch.dict(check_versions.VERSION_FILE_ALIASES,
                        {"ci": FIXTURE_REPO / "absent.yml"}):
            versions = read_pinned_image_versions()
        self.assertNotIn("pr_agent_version", versions)

    def test_an_entry_that_resolved_nothing_is_an_error_not_an_update(self):
        """A renamed manifest must break the pin loudly, not silently untrack it."""
        with patch.dict(check_versions.VERSION_FILE_ALIASES,
                        {"ci": FIXTURE_REPO / "absent.yml"}):
            versions = read_pinned_image_versions()
            svc = next(s for s in check_versions.SERVICE_REGISTRY
                       if s["var_name"] == "pr_agent_version")
            result = check_versions.check_service(svc, versions, use_cache=False)
        self.assertIn("pr_agent_version", result.error)
        self.assertIn("absent.yml", result.error)
        self.assertFalse(result.update_available)

    def test_a_deliberately_unreadable_pin_is_not_an_error(self):
        svc = next(s for s in check_versions.SERVICE_REGISTRY
                   if s["var_name"] == "pr_agent_version")
        declared = dict(svc, unreadable_current=True)
        with patch.dict(check_versions.VERSION_FILE_ALIASES,
                        {"ci": FIXTURE_REPO / "absent.yml"}), \
                patch.object(check_versions, "SERVICE_REGISTRY", [declared]):
            versions = read_pinned_image_versions()
            result = check_versions.check_service(declared, versions, use_cache=False)
        self.assertNotIn("pr_agent_version", check_versions.UNRESOLVED_PINS)
        self.assertNotIn("no longer tracked", result.error or "")

    def test_pin_regex_reads_a_ci_variable_and_a_services_entry(self):
        """Pins that are not `image:` lines: a CI variable and a services entry."""
        try:
            check_versions.load_config(
                FIXTURE_DIR / "registry-pin-regex.py", repo_root=FIXTURE_REPO
            )
            versions = read_pinned_image_versions()
        finally:
            check_versions.load_config(FIXTURE_CONFIG, repo_root=FIXTURE_REPO)
        self.assertEqual(versions["kustomize_version"], "5.4.3")
        self.assertEqual(versions["docker_dind_version"], "24.0-dind")

    def test_pin_regex_without_a_capture_group_is_an_operator_error(self):
        # A regex that matches but captures nothing would otherwise raise
        # IndexError deep in the read.
        groupless = [{
            "name": "Kustomize",
            "var_name": "kustomize_version",
            "category": "github",
            "version_file": "ci",
            "pin_regex": r'^\s*KUSTOMIZE_VERSION:\s*"[\w.+-]+"\s*$',
        }]
        with patch.object(check_versions, "SERVICE_REGISTRY", groupless):
            with self.assertRaises(RuntimeError) as ctx:
                read_pinned_image_versions()
        self.assertIn("capture group", str(ctx.exception))

    def test_default_image_matcher_misses_a_non_image_pin(self):
        # Without pin_regex the same entry reads nothing, which is the state
        # that made such a pin report an update forever.
        groupless = [
            {k: v for k, v in svc.items() if k != "pin_regex"}
            for svc in [
                {
                    "name": "Kustomize",
                    "var_name": "kustomize_version",
                    "category": "github",
                    "version_file": "ci",
                    "docker_image": "kustomize",
                    "pin_regex": r"never used",
                }
            ]
        ]
        with patch.object(check_versions, "SERVICE_REGISTRY", groupless):
            self.assertNotIn("kustomize_version", read_pinned_image_versions())

    def test_pinned_image_update_is_manual(self):
        """A digest-pinned image is flagged for manual update, never written.

        VARS_FILE points at a COPY: a regressed guard must fail the assertion
        below, not rewrite the tracked fixture in the working tree.
        """
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "vars.yml"
            shutil.copy(FIXTURE_REPO / "vars.yml", copy)
            with patch.object(check_versions, "VARS_FILE", copy):
                self.assertFalse(update_version_in_file("pr_agent_version", "9.9.9"))
                self.assertFalse(update_version_in_file("python_cronjob_version", "9.9-slim"))
            self.assertEqual(copy.read_bytes(), (FIXTURE_REPO / "vars.yml").read_bytes())


@pytest.mark.parametrize("text,expected", [
    ("1.2.3", ((0, 1), (0, 2), (0, 3))),
    ("10.20.30", ((0, 10), (0, 20), (0, 30))),
    ("v1.2.3", ((0, 1), (0, 2), (0, 3))),
    ("v10.0.1", ((0, 10), (0, 0), (0, 1))),
    ("4.0.16.2944", ((0, 4), (0, 0), (0, 16), (0, 2944))),
    ("1.80.0", ((0, 1), (0, 80), (0, 0))),
    ("1:1.80.0", ((0, 1), (0, 80), (0, 0))),
], ids=["simple", "wide", "v-prefix", "v-prefix-zero", "four-part", "no-epoch", "epoch-stripped"])
def test_parse_version_tuple(text, expected):
    assert parse_version_tuple(text) == expected


@pytest.mark.parametrize("text,leading", [
    ("v1.35.2+k3s1", ((0, 1), (0, 35), (0, 2))),
    ("1.43.0.10492-121068a07", ((0, 1), (0, 43), (0, 0), (0, 10492))),
    ("18.9.1-ee.0", ((0, 18), (0, 9), (0, 1))),
], ids=["k3s", "build-hash-suffix", "edition-suffix"])
def test_parse_version_tuple_reads_the_numeric_lead(text, leading):
    assert parse_version_tuple(text)[:len(leading)] == leading


@pytest.mark.parametrize("a,b,greater", [
    ("2.0.0", "1.9.9", True),
    ("1.10.0", "1.9.0", True),
    ("1.0.0", "1.0.0", False),
    ("1.0.0", "2.0.0", False),
    ("v2.0.0", "v1.9.9", True),
    ("v1.10.0", "v1.9.0", True),
    ("v1.35.2+k3s10", "v1.35.2+k3s9", True),
    ("v1.35.2+k3s1", "v1.35.1+k3s1", True),
    ("v1.35.2+k3s1", "v1.35.2+k3s2", False),
    ("17.1", "17", True),
    ("17", "17.1", False),
    ("1.0.0", "1.0.0-rc1", True),
    ("1.0.0-rc1", "1.0.0", False),
    ("17", "17-alpha", True),
    ("17-alpha", "17", False),
    ("17.1-trixie", "17-trixie", True),
    ("18-trixie", "17.1-trixie", True),
])
def test_version_greater(a, b, greater):
    assert version_greater(a, b) is greater


class TestAptParsing(unittest.TestCase):
    """fetch_apt_repo_version against realistic Packages indexes: the generic
    fetcher every apt-sourced service goes through."""

    PLEX_PACKAGES_CONTENT = """Package: plexmediaserver
Version: 1.42.0.10000-abc123
Architecture: amd64
Maintainer: Plex Inc.
Installed-Size: 123456
Depends: libc6
Section: video
Description: Plex Media Server
 Stream your media.

Package: plexmediaserver
Version: 1.43.0.10492-121068a07
Architecture: amd64
Maintainer: Plex Inc.
Installed-Size: 123456
Depends: libc6
Section: video
Description: Plex Media Server
 Stream your media.

Package: plexmediaserver
Version: 1.41.0.9000-xyz789
Architecture: amd64
Maintainer: Plex Inc.
Installed-Size: 123456
Depends: libc6
Section: video
Description: Plex Media Server
 Stream your media.
"""

    GITLAB_PACKAGES_CONTENT = """Package: gitlab-ee
Version: 18.9.1-ee.0
Architecture: amd64
Maintainer: GitLab Inc.
Installed-Size: 1234567890
Section: devel
Description: GitLab Enterprise Edition

Package: gitlab-ee
Version: 18.8.2-ee.0
Architecture: amd64
Maintainer: GitLab Inc.
Installed-Size: 1234567890
Section: devel
Description: GitLab Enterprise Edition

Package: gitlab-ee
Version: 18.10.0~rc1-ee.0
Architecture: amd64
Maintainer: GitLab Inc.
Installed-Size: 1234567890
Section: devel
Description: GitLab Enterprise Edition (RC)

Package: gitlab-runner
Version: 16.11.0
Architecture: amd64
Maintainer: GitLab Inc.
Section: devel
Description: GitLab Runner
"""

    def test_highest_version_of_a_package(self):
        """A media-server-style index: the highest of three stanzas wins."""
        svc = {"apt_url": "https://example.com/Packages", "apt_package": "plexmediaserver"}
        with patch.object(check_versions, "_fetch_packages_index",
                          return_value=self.PLEX_PACKAGES_CONTENT):
            assert check_versions.fetch_apt_repo_version(svc) == "1.43.0.10492-121068a07"

    TAILSCALE_PACKAGES = (
        "Package: tailscale\nVersion: 1.80.0\nArchitecture: amd64\n\n"
        "Package: tailscale\nVersion: 1.82.3\nArchitecture: amd64\n\n"
        "Package: other\nVersion: 9.9.9\nArchitecture: amd64\n"
    )

    def test_either_url_key_reaches_the_index(self):
        for key in ("apt_url", "apt_index_url"):
            svc = {key: "https://example.com/Packages", "apt_package": "tailscale"}
            with self.subTest(key=key), patch.object(
                check_versions, "_urlopen_with_retry", return_value=self.TAILSCALE_PACKAGES.encode()
            ):
                self.assertEqual(check_versions.fetch_apt_repo_version(svc), "1.82.3")

    def test_missing_package_raises(self):
        svc = {"apt_url": "https://example.com/Packages", "apt_package": "plexmediaserver"}
        with patch.object(check_versions, "_fetch_packages_index",
                          return_value="Package: someother\nVersion: 1.0.0\n"):
            with self.assertRaises(RuntimeError) as ctx:
                check_versions.fetch_apt_repo_version(svc)
            self.assertIn("package 'plexmediaserver' not found", str(ctx.exception))

    def test_exclude_regex_skips_prereleases(self):
        """apt_exclude_regex is how a consumer drops rc/beta stanzas."""
        svc = {
            "apt_url": "https://example.com/Packages",
            "apt_package": "gitlab-ee",
            "apt_exclude_regex": r"(rc|beta|alpha)",
        }
        with patch.object(check_versions, "_fetch_packages_index",
                          return_value=self.GITLAB_PACKAGES_CONTENT):
            assert check_versions.fetch_apt_repo_version(svc) == "18.9.1-ee.0"

    def test_without_exclude_regex_the_rc_wins(self):
        svc = {"apt_url": "https://example.com/Packages", "apt_package": "gitlab-ee"}
        with patch.object(check_versions, "_fetch_packages_index",
                          return_value=self.GITLAB_PACKAGES_CONTENT):
            # `~` is a Debian pre-release marker, so 18.10.0~rc1 < 18.10.0 but
            # still above 18.9.1: without the filter it is the highest present.
            assert check_versions.fetch_apt_repo_version(svc) == "18.10.0~rc1-ee.0"

    def test_url_list_falls_back_to_the_next_suite(self):
        """A suite that 404s must not fail the check while another has the package."""
        tried = []

        def fake_index(url):
            tried.append(url)
            if "trixie" in url:
                raise RuntimeError("HTTP 404")
            return self.GITLAB_PACKAGES_CONTENT

        svc = {
            "apt_url": ["https://example.com/trixie/Packages",
                        "https://example.com/bookworm/Packages"],
            "apt_package": "gitlab-ee",
            "apt_exclude_regex": r"(rc|beta|alpha)",
        }
        with patch.object(check_versions, "_fetch_packages_index", side_effect=fake_index):
            assert check_versions.fetch_apt_repo_version(svc) == "18.9.1-ee.0"
        assert len(tried) == 2

    def test_every_url_failing_names_each_attempt(self):
        svc = {
            "apt_url": ["https://example.com/a/Packages", "https://example.com/b/Packages"],
            "apt_package": "gitlab-ee",
        }
        with patch.object(check_versions, "_fetch_packages_index",
                          side_effect=RuntimeError("HTTP 404")):
            with self.assertRaises(RuntimeError) as ctx:
                check_versions.fetch_apt_repo_version(svc)
        self.assertIn("/a/Packages", str(ctx.exception))
        self.assertIn("/b/Packages", str(ctx.exception))


PACKAGES_BODY = b"Package: tailscale\nVersion: 1.80.0\n"
PACKAGES_URL = "https://example.com/Packages"


def _serve(answers: list):
    """A urlopen stand-in answering each call with the next body or exception."""
    calls: list[str] = []

    def urlopen(req, timeout=None):
        calls.append(req.full_url)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return mock_response(answer)

    return calls, urlopen


@pytest.mark.parametrize("answers,urls", [
    ([PACKAGES_BODY], [PACKAGES_URL]),
    ([gzip.compress(PACKAGES_BODY)], [PACKAGES_URL]),
    ([b"<!DOCTYPE html><html><body>Error</body></html>", gzip.compress(PACKAGES_BODY)],
     [PACKAGES_URL, PACKAGES_URL + ".gz"]),
    ([b"", gzip.compress(PACKAGES_BODY)], [PACKAGES_URL, PACKAGES_URL + ".gz"]),
    ([urllib.error.HTTPError(PACKAGES_URL, 503, "boom", {}, None), PACKAGES_BODY],
     [PACKAGES_URL, PACKAGES_URL]),
], ids=["plain", "gzip-sniffed", "html-falls-back-to-gz", "empty-falls-back-to-gz",
        "5xx-retried"])
def test_fetch_packages_index(answers, urls):
    calls, urlopen = _serve(list(answers))
    with patch("urllib.request.urlopen", side_effect=urlopen), \
         patch.object(check_versions.time, "sleep"):
        assert check_versions._fetch_packages_index(PACKAGES_URL) == PACKAGES_BODY.decode()
    assert calls == urls


@pytest.mark.parametrize("url,urls", [
    (PACKAGES_URL + ".gz", [PACKAGES_URL + ".gz"]),
    (PACKAGES_URL, [PACKAGES_URL, PACKAGES_URL + ".gz"]),
], ids=["gz-url", "both-urls"])
def test_no_packages_stanzas_raises_naming_each_url(url, urls):
    calls, urlopen = _serve([b"<html>nope"] * len(urls))
    with patch("urllib.request.urlopen", side_effect=urlopen):
        with pytest.raises(RuntimeError) as excinfo:
            check_versions._fetch_packages_index(url)
    assert calls == urls
    assert all(u in str(excinfo.value) for u in urls)


class TestGetDeployCommand(unittest.TestCase):
    """Deploy routing is registry data: per-service `deploy_command`, a derived
    instruction for `version_file` pins, then the config default. A regression
    here would tell an operator to run the wrong (or a no-op) rollout."""

    def _mk(self, var_name: str, category: str = ""):
        result = MagicMock()
        result.var_name = var_name
        result.category = category
        return result

    def test_registry_deploy_command_wins(self):
        self.assertEqual(
            check_versions.get_deploy_command(self._mk("k3s_version")),
            "task maintenance:update-k3s-nodes",
        )
        self.assertIn(
            "flux:sync-versions",
            check_versions.get_deploy_command(self._mk("helm_chart_versions.traefik", "helm")),
        )

    def test_version_file_pin_gets_derived_instruction(self):
        cmd = check_versions.get_deploy_command(self._mk("pr_agent_version"))
        self.assertIn("@sha256 digest", cmd)
        self.assertIn(".gitlab-ci.yml", cmd)

    def test_multi_file_pin_names_every_file(self):
        cmd = check_versions.get_deploy_command(self._mk("python_cronjob_version"))
        self.assertIn("kubernetes/apps/one/cronjob.yaml", cmd)
        self.assertIn("kubernetes/apps/two/cronjob.yaml", cmd)

    def test_unmapped_service_falls_back_to_default(self):
        self.assertEqual(
            check_versions.get_deploy_command(self._mk("manual_thing_version")),
            check_versions.DEFAULT_DEPLOY_COMMAND,
        )
        self.assertEqual(
            check_versions.get_deploy_command(self._mk("not_in_registry_version")),
            check_versions.DEFAULT_DEPLOY_COMMAND,
        )


class TestRegistryCoverage(unittest.TestCase):
    """--check-coverage: a pin with no registry entry is silently never reported
    as outdated, so the gate turns that into a CI failure."""

    def test_fixture_repo_is_fully_covered(self):
        self.assertEqual(check_versions.missing_registry_entries(), [])

    def test_untracked_pin_is_reported(self):
        with patch.object(check_versions, "read_current_versions",
                          return_value={"k3s_version": "v1", "ghost_version": "9"}):
            self.assertEqual(check_versions.missing_registry_entries(), ["ghost_version"])

    def test_allowlisted_pin_is_not_reported(self):
        # debian_version is in the fixture config's untracked_allowlist.
        with patch.object(check_versions, "read_current_versions",
                          return_value={"debian_version": "13"}):
            self.assertEqual(check_versions.missing_registry_entries(), [])

    def test_cli_exits_1_on_missing_entry(self):
        with patch.object(check_versions, "read_current_versions",
                          return_value={"ghost_version": "9"}), \
             patch.object(check_versions.sys, "argv",
                          ["check-versions.py", "--check-coverage"]):
            with self.assertRaises(SystemExit) as cm:
                check_versions.main()
        self.assertEqual(cm.exception.code, 1)

    def test_cli_exits_0_when_covered(self):
        with patch.object(check_versions, "read_current_versions", return_value={}), \
             patch.object(check_versions.sys, "argv",
                          ["check-versions.py", "--check-coverage"]):
            with self.assertRaises(SystemExit) as cm:
                check_versions.main()
        self.assertEqual(cm.exception.code, 0)


class TestLoadConfig(unittest.TestCase):
    """Config loading is the seam every consumer crosses; a malformed config must
    fail loudly rather than silently checking zero services."""

    def tearDown(self):
        check_versions.load_config(FIXTURE_CONFIG, repo_root=FIXTURE_REPO)

    def test_json_config_supported(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "registry.json"
            path.write_text(json.dumps({
                "vars_file": "vars.yml",
                "default_deploy_command": "make deploy",
                "services": [{"name": "X", "var_name": "x_version", "category": "manual"}],
            }))
            cfg = check_versions.load_config(path, repo_root=Path(d))
        self.assertEqual(cfg["services"][0]["var_name"], "x_version")
        self.assertEqual(check_versions.DEFAULT_DEPLOY_COMMAND, "make deploy")

    def test_bare_service_registry_module_supported(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "registry.py"
            path.write_text(
                'SERVICE_REGISTRY = [{"name": "X", "var_name": "x_version", '
                '"category": "manual"}]\n'
            )
            check_versions.load_config(path, repo_root=Path(d))
        self.assertEqual([s["var_name"] for s in check_versions.SERVICE_REGISTRY], ["x_version"])

    def test_empty_services_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "registry.json"
            path.write_text(json.dumps({"services": []}))
            with self.assertRaisesRegex(SystemExit, "non-empty list"):
                check_versions.load_config(path, repo_root=Path(d))

    def test_service_without_var_name_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "registry.json"
            path.write_text(json.dumps({"services": [{"name": "X"}]}))
            with self.assertRaisesRegex(SystemExit, "name \\+ var_name"):
                check_versions.load_config(path, repo_root=Path(d))

    def test_unsupported_suffix_rejected(self):
        with self.assertRaisesRegex(SystemExit, "unsupported config format"):
            check_versions.load_config(FIXTURE_DIR / "registry.toml")

    def test_resolve_config_prefers_explicit_then_env(self):
        self.assertEqual(
            check_versions.resolve_config_path("/tmp/explicit.json"), Path("/tmp/explicit.json")
        )
        with patch.dict(os.environ, {"CHECK_VERSIONS_CONFIG": "/tmp/env.json"}):
            self.assertEqual(check_versions.resolve_config_path(), Path("/tmp/env.json"))

    def test_resolve_config_errors_when_absent(self):
        with tempfile.TemporaryDirectory() as d, \
             patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CHECK_VERSIONS_CONFIG", None)
            with self.assertRaisesRegex(SystemExit, "no version-registry config found"):
                check_versions.resolve_config_path(repo_root=Path(d))


@pytest.mark.parametrize("a,b,expected", [
    ("1.98.4", "1.98.5", -1),
    ("1.98.5", "1.98.4", 1),
    ("1.98.4", "1.98.4", 0),
    ("1:0.4.6-1", "0.4.6", 1),
    ("0:1.98.4", "1.98.5", -1),
    ("0.5.0~rc1-1", "0.5.0-1", -1),
    ("0.4.6-1ubuntu1", "0.4.6-1", 1),
], ids=["patch-lower", "patch-higher", "equal", "epoch-wins", "zero-epoch-is-default",
        "tilde-is-prerelease", "revision-tail"])
def test_debian_version_compare(a, b, expected):
    assert check_versions.debian_version_compare(a, b) == expected


def test_debian_version_compare_rejects_a_malformed_epoch():
    with pytest.raises(ValueError):
        check_versions.debian_version_compare("x:1.0", "1.0")


class TestHelmIndexParser(unittest.TestCase):
    """fetch_helm_version tolerates extra post-colon whitespace and skips appVersion."""

    INDEX = (
        "apiVersion: v1\n"
        "entries:\n"
        "  mychart:\n"
        "  - apiVersion: v2\n"
        "    appVersion: 9.9.9\n"
        "    version:   1.2.3\n"  # three spaces after the colon
        "  - apiVersion: v2\n"
        "    appVersion: 9.9.9\n"
        "    version: 1.2.2\n"
    )

    # A chart entry with a dependencies: sub-block whose dependency version
    # outranks the chart's own version. The parser must NOT collect the
    # dependency version.
    INDEX_WITH_DEPENDENCY = (
        "apiVersion: v1\n"
        "entries:\n"
        "  mychart:\n"
        "  - apiVersion: v2\n"
        "    appVersion: 9.9.9\n"
        "    dependencies:\n"
        "    - name: postgresql\n"
        "      repository: https://example.com\n"
        "      version: 99.0.0\n"
        "    version: 1.2.3\n"
        "  - apiVersion: v2\n"
        "    version: 1.2.2\n"
    )

    def test_extra_spaces_and_appversion_excluded(self):
        svc = {"helm_repo": "https://example.com", "helm_chart": "mychart"}
        with patch.object(check_versions, "_make_request", return_value=self.INDEX):
            latest = check_versions.fetch_helm_version(svc)
        # 1.2.3 (highest version:), not 9.9.9 (appVersion), and the 3-space line parsed
        self.assertEqual(latest, "1.2.3")

    def test_dependency_version_not_captured(self):
        svc = {"helm_repo": "https://example.com", "helm_chart": "mychart"}
        with patch.object(check_versions, "_make_request", return_value=self.INDEX_WITH_DEPENDENCY):
            latest = check_versions.fetch_helm_version(svc)
        # 1.2.3 (chart's own version), NOT 99.0.0 (postgresql dependency version)
        self.assertEqual(latest, "1.2.3")


class TestContainerFetcherGuards(unittest.TestCase):
    """Docker Hub / LSIO fetchers raise a clear error on a non-JSON body."""

    def test_dockerhub_non_json_raises(self):
        svc = {"docker_image": "library/foo", "tag_regex": r"^(\d+\.\d+\.\d+)$"}
        with patch.object(check_versions, "_make_request", return_value="<html>maintenance</html>"):
            with self.assertRaises(RuntimeError):
                check_versions.fetch_dockerhub_version(svc)

    def test_lsio_non_json_raises(self):
        svc = {"docker_image": "linuxserver/foo", "lsio_version_regex": r"^version-(\d+\.\d+\.\d+)$"}
        with patch.object(check_versions, "_make_request", return_value="not json"):
            with self.assertRaises(RuntimeError):
                check_versions.fetch_lsio_version(svc)


class TestDockerhubMajorPinAndPrefix(unittest.TestCase):
    """fetch_dockerhub_version confines results when pinning a major or a series."""

    @staticmethod
    def _payload(*tags):
        return {"results": [{"name": t} for t in tags]}

    def test_pin_major_confines_to_current_major(self):
        # A pinned major never proposes the next major.
        svc = {
            "docker_image": "library/postgres",
            "tag_regex": r"^(\d+(?:\.\d+)?)-trixie$",
            "pin_major_version": True,
            "_current_version": "17-trixie",
        }
        payload = self._payload("18-trixie", "17.2-trixie", "17-trixie", "16.9-trixie")
        with patch.object(check_versions, "_make_request", return_value=payload):
            # Highest tag within major 17 — never 18-trixie.
            self.assertEqual(check_versions.fetch_dockerhub_version(svc), "17.2-trixie")

    def test_version_prefix_confines_to_series(self):
        # version_prefix holds the entry to one minor series.
        svc = {
            "docker_image": "getmeili/meilisearch",
            "tag_regex": r"^v(\d+\.\d+\.\d+)$",
            "version_prefix": "v1.15.",
        }
        payload = self._payload("v1.16.0", "v1.15.2", "v1.15.0")
        with patch.object(check_versions, "_make_request", return_value=payload):
            self.assertEqual(check_versions.fetch_dockerhub_version(svc), "v1.15.2")

    def test_pin_major_handles_v_prefixed_versions(self):
        # A leading "v" must not bypass the major pin.
        svc = {
            "docker_image": "qmcgaw/gluetun",
            "tag_regex": r"^(v\d+\.\d+\.\d+)$",
            "pin_major_version": True,
            "_current_version": "v3.41.1",
        }
        payload = self._payload("v4.0.0", "v3.42.0", "v3.41.1", "v2.9.0")
        with patch.object(check_versions, "_make_request", return_value=payload):
            # Highest tag within major 3 — never v4.0.0.
            self.assertEqual(check_versions.fetch_dockerhub_version(svc), "v3.42.0")

    def test_name_filter_has_no_startswith_constraint(self):
        # name_filter narrows only the API query; the regex selects the tag.
        # Applying startswith to it would reject every "3.x-slim" tag.
        svc = {
            "docker_image": "library/python",
            "tag_regex": r"^(3\.\d+)-slim$",
            "dockerhub_name_filter": "-slim",
        }
        captured = {}

        def fake_request(url, headers=None):
            captured["url"] = url
            return self._payload("3.13-slim", "3.12-slim", "3.13")

        with patch.object(check_versions, "_make_request", side_effect=fake_request):
            self.assertEqual(check_versions.fetch_dockerhub_version(svc), "3.13-slim")
        # name_filter feeds the Docker Hub `name=` query, not a tag startswith.
        self.assertIn("name=-slim", captured["url"])


class TestUpdateVersionInFile(unittest.TestCase):
    """update_version_in_file round-trips on a temp file, preserving format."""

    def test_top_level_roundtrip_preserves_quotes_and_comment(self):
        path = write_tmp_vars(
            'gluetun_version: "v3.40.0"  # Currently deployed v3.40.0\n'
            "other_version: 1.2.3\n"
        )
        try:
            with patch.object(check_versions, "VARS_FILE", path):
                self.assertTrue(
                    check_versions.update_version_in_file("gluetun_version", "v3.41.0")
                )
            out = path.read_text()
            self.assertIn('gluetun_version: "v3.41.0"', out)
            self.assertIn("# Currently deployed v3.41.0", out)
            self.assertIn("other_version: 1.2.3", out)  # untouched
        finally:
            os.unlink(path)

    def test_missing_var_returns_false(self):
        path = write_tmp_vars("foo_version: 1.0.0\n")
        try:
            with patch.object(check_versions, "VARS_FILE", path):
                self.assertFalse(
                    check_versions.update_version_in_file("nonexistent_version", "9.9.9")
                )
        finally:
            os.unlink(path)

    def test_helm_nested_key(self):
        path = write_tmp_vars(
            "helm_chart_versions:\n"
            '  traefik: "40.3.0"  # Currently deployed 40.3.0\n'
        )
        try:
            with patch.object(check_versions, "VARS_FILE", path):
                self.assertTrue(
                    check_versions.update_version_in_file("helm_chart_versions.traefik", "40.4.0")
                )
            out = path.read_text()
            self.assertIn('traefik: "40.4.0"', out)
            # The nested branch must rewrite the trailing comment too (the
            # top-level case is asserted separately).
            self.assertIn("# Currently deployed 40.4.0", out)
        finally:
            os.unlink(path)

    def test_nested_same_named_key_is_never_rewritten(self):
        """A `foo_version` nested under another mapping is not the top-level
        pin; rewriting it would de-nest the key and break the vars file."""
        path = write_tmp_vars(
            "some_block:\n"
            '  foo_version: "1.0.0"\n'
        )
        try:
            with patch.object(check_versions, "VARS_FILE", path):
                self.assertFalse(
                    check_versions.update_version_in_file("foo_version", "2.0.0")
                )
            self.assertIn('  foo_version: "1.0.0"', path.read_text())
        finally:
            os.unlink(path)


class TestHeldUpdateGuard(unittest.TestCase):
    """A held service is never written by --update-all."""

    def test_update_all_skips_held(self):
        SV = check_versions.ServiceVersion
        held = SV(name="HeldChart", category="helm", current_version="0.15.3",
                  latest_version="0.16.0", update_available=True,
                  var_name="helm_chart_versions.held_chart", held=True)
        normal = SV(name="Foo", category="helm", current_version="1.0",
                    latest_version="1.1", update_available=True, var_name="foo_version")
        with patch.object(check_versions, "check_all", return_value=[held, normal]), \
             patch.object(check_versions, "read_current_versions", return_value={}), \
             patch.object(check_versions, "get_deploy_command", return_value="deploy"), \
             patch.object(check_versions, "update_version_in_file", return_value=True) as muf, \
             patch.object(check_versions.sys, "argv", ["check-versions.py", "--update-all"]):
            with self.assertRaises(SystemExit):
                check_versions.main()
        written_vars = [call.args[0] for call in muf.call_args_list]
        self.assertIn("foo_version", written_vars)
        self.assertNotIn("helm_chart_versions.held_chart", written_vars)

    def test_json_reports_a_held_update_as_not_available(self):
        """A held service reports `update_available: false` in the JSON.

        A bump bot iterating `services` never reads the summary; the hold
        stays visible through the `held` and `latest_version` fields.
        """
        SV = check_versions.ServiceVersion
        held = SV(name="HeldChart", category="helm", current_version="0.15.3",
                  latest_version="0.16.0", update_available=True,
                  var_name="helm_chart_versions.held_chart", held=True)
        normal = SV(name="Foo", category="helm", current_version="1.0",
                    latest_version="1.1", update_available=True, var_name="foo_version")
        data = json.loads(check_versions.format_json([held, normal]))
        by_name = {s["name"]: s for s in data["services"]}
        self.assertFalse(by_name["HeldChart"]["update_available"])
        self.assertTrue(by_name["HeldChart"]["held"])
        self.assertEqual(by_name["HeldChart"]["latest_version"], "0.16.0")
        self.assertTrue(by_name["Foo"]["update_available"])
        self.assertEqual(data["summary"]["updates_available"], 1)
        self.assertEqual(data["summary"]["updates_held"], 1)

    def test_single_update_skips_held(self):
        """`--update <service>` refuses to write a held version.

        It calls check_service rather than check_all, so the guard is an
        independent code path from --update-all.
        """
        SV = check_versions.ServiceVersion
        # --update resolves the name against the fixture registry.
        held = SV(name="HeldChart", category="helm", current_version="0.15.3",
                  latest_version="0.16.0", update_available=True,
                  var_name="helm_chart_versions.metallb", held=True)
        with patch.object(check_versions, "check_service", return_value=held), \
             patch.object(check_versions, "read_current_versions", return_value={}), \
             patch.object(check_versions, "update_version_in_file", return_value=True) as muf, \
             patch.object(check_versions.sys, "argv",
                          ["check-versions.py", "--update",
                           "helm_chart_versions.metallb"]):
            with self.assertRaises(SystemExit) as cm:
                check_versions.main()
        muf.assert_not_called()                  # held => never written
        self.assertEqual(cm.exception.code, 0)   # a held hold is a clean exit


class TestMakeRequestRetry(unittest.TestCase):
    """_make_request retries on transient failures but never on 4xx.

    Pins the semantics: retry on URLError, socket.timeout and HTTP 5xx, never
    on 4xx, and re-raise the last exception once attempts are exhausted.
    """

    def _http_error(self, code: int):
        return urllib.error.HTTPError(
            "https://example.com", code, "boom", {}, None
        )

    def test_persistent_5xx_exhausts_retries(self):
        """Persistent HTTP 5xx retries RETRY_ATTEMPTS times, then raises."""
        calls = [0]

        def side_effect(req, timeout=None):
            calls[0] += 1
            raise self._http_error(503)

        with patch("urllib.request.urlopen", side_effect=side_effect), \
             patch.object(check_versions.time, "sleep"):
            with self.assertRaises(RuntimeError):  # last HTTPError wrapped by _make_request
                check_versions._make_request("https://example.com/flaky")

        assert calls[0] == check_versions.RETRY_ATTEMPTS, (
            f"should attempt exactly RETRY_ATTEMPTS={check_versions.RETRY_ATTEMPTS} times"
        )

    def test_helper_reraises_last_exception_after_exhaustion(self):
        """_urlopen_with_retry re-raises the original exception type unchanged
        so existing callers behave identically after retries are exhausted."""
        def side_effect(req, timeout=None):
            raise urllib.error.URLError("connection refused")

        with patch("urllib.request.urlopen", side_effect=side_effect), \
             patch.object(check_versions.time, "sleep"):
            req = check_versions.urllib.request.Request("https://example.com")
            with self.assertRaises(urllib.error.URLError):
                check_versions._urlopen_with_retry(req)


@pytest.mark.parametrize("exc,failures,body,expected", [
    (urllib.error.URLError("temporary failure in name resolution"), 2,
     b'{"tag_name": "v1.2.3"}', {"tag_name": "v1.2.3"}),
    (http.client.IncompleteRead(b"partial", expected=1000), 1,
     b'{"tag_name": "v9.9.9"}', {"tag_name": "v9.9.9"}),
    (socket.timeout("timed out"), 1, b"plain text body", "plain text body"),
], ids=["urlerror", "incomplete-read", "socket-timeout"])
def test_make_request_retries_a_transient_failure(exc, failures, body, expected):
    calls = []

    def side_effect(req, timeout=None):
        calls.append(req)
        if len(calls) <= failures:
            raise exc
        return mock_response(body)

    with patch("urllib.request.urlopen", side_effect=side_effect), \
         patch.object(check_versions.time, "sleep"):
        assert check_versions._make_request("https://example.com/api") == expected
    assert len(calls) == failures + 1


@pytest.mark.parametrize("code,needle", [(404, "HTTP 404"), (403, "rate limited")],
                         ids=["404", "403-rate-limit"])
def test_make_request_never_retries_a_4xx(code, needle):
    calls = []

    def side_effect(req, timeout=None):
        calls.append(req)
        raise urllib.error.HTTPError("https://example.com", code, "boom", {}, None)

    with patch("urllib.request.urlopen", side_effect=side_effect), \
         patch.object(check_versions.time, "sleep") as sleep:
        with pytest.raises(RuntimeError, match=needle):
            check_versions._make_request("https://example.com/api")
    assert len(calls) == 1
    sleep.assert_not_called()


class TestFetchGithubReleaseLatest(unittest.TestCase):
    """The latest-release (no tag_filter) path must fail loud on a missing
    tag_name, matching the tag_filter branch — otherwise the service silently
    reports up-to-date with a blank Latest column."""

    def test_a_page_size_outside_githubs_range_is_refused(self):
        """GitHub clamps per_page above 100, which would make the first page
        read as the last; the entry is refused instead of silently truncated."""
        for bad in (0, 101):
            svc = {"github_repo": "o/r", "tag_filter": r"^v", "releases_per_page": bad}
            with patch.object(check_versions, "github_api") as api:
                with self.assertRaises(RuntimeError):
                    check_versions.fetch_github_release(svc)
                api.assert_not_called()

    def test_the_scan_horizon_does_not_shrink_with_the_page_size(self):
        """Smaller pages mean more pages: the newest 500 releases are always
        covered, and nothing past them is requested."""
        svc = {"github_repo": "o/r", "tag_filter": r"^v\d+\.\d+\.\d+$", "releases_per_page": 25}
        seen = []

        def fake(path):
            seen.append(path)
            page = int(path.rsplit("page=", 1)[1])
            return [{"tag_name": f"v1.{page}.{i}"} for i in range(25)]

        with patch.object(check_versions, "github_api", side_effect=fake):
            self.assertEqual(check_versions.fetch_github_release(svc), "v1.20.24")
        self.assertEqual(len(seen), 20)
        self.assertTrue(seen[-1].endswith("per_page=25&page=20"))

    def test_a_page_size_that_does_not_divide_the_horizon_scans_exactly_500(self):
        """With 99 per page the sixth page would reach release 594; only the
        5 releases inside the horizon count, so a higher tag past it never wins."""
        svc = {"github_repo": "o/r", "tag_filter": r"^v\d+\.\d+\.\d+$", "releases_per_page": 99}
        seen = []

        def fake(path):
            seen.append(path)
            page = int(path.rsplit("page=", 1)[1])
            start = (page - 1) * 99
            # release index i carries v1.i.0 except the one just past the horizon
            return [{"tag_name": "v9.0.0" if start + i == 500 else f"v1.{start + i}.0"} for i in range(99)]

        with patch.object(check_versions, "github_api", side_effect=fake):
            self.assertEqual(check_versions.fetch_github_release(svc), "v1.499.0")
        self.assertEqual(len(seen), 6)

    def test_tag_filter_scan_honours_the_entry_page_size(self):
        """A repo with huge per-release asset lists needs smaller pages; the
        entry's releases_per_page sets the page size and the last-page test."""
        svc = {"github_repo": "o/r", "tag_filter": r"^v\d+\.\d+\.\d+$", "releases_per_page": 2}
        pages = {1: [{"tag_name": "v1.0.0"}, {"tag_name": "v1.2.0"}], 2: [{"tag_name": "v1.1.0"}]}
        seen = []

        def fake(path):
            seen.append(path)
            page = int(path.rsplit("page=", 1)[1])
            return pages.get(page, [])

        with patch.object(check_versions, "github_api", side_effect=fake):
            self.assertEqual(check_versions.fetch_github_release(svc), "v1.2.0")
        self.assertEqual(seen, ["/repos/o/r/releases?per_page=2&page=1", "/repos/o/r/releases?per_page=2&page=2"])

    def test_missing_tag_name_raises(self):
        svc = {"github_repo": "owner/repo"}  # no tag_filter -> latest endpoint
        with patch.object(check_versions, "github_api", return_value={}):
            with self.assertRaises(RuntimeError) as ctx:
                check_versions.fetch_github_release(svc)
        self.assertIn("no tag_name", str(ctx.exception))

    def test_empty_tag_name_raises(self):
        svc = {"github_repo": "owner/repo"}
        with patch.object(check_versions, "github_api", return_value={"tag_name": ""}):
            with self.assertRaises(RuntimeError):
                check_versions.fetch_github_release(svc)

    def test_present_tag_name_returned(self):
        svc = {"github_repo": "owner/repo"}
        with patch.object(check_versions, "github_api",
                          return_value={"tag_name": "v2.3.4"}):
            self.assertEqual(check_versions.fetch_github_release(svc), "v2.3.4")

    def test_present_tag_name_strip_prefix(self):
        svc = {"github_repo": "owner/repo", "version_prefix": "v", "strip_prefix": True}
        with patch.object(check_versions, "github_api",
                          return_value={"tag_name": "v2.3.4"}):
            self.assertEqual(check_versions.fetch_github_release(svc), "2.3.4")


class TestMakeRequestErrorTagging(unittest.TestCase):
    """_make_request's catch-all must include the exception TYPE in the message
    so check_service (which catches RuntimeError before its typed fallback)
    still surfaces the diagnostic type tag."""

    def test_unexpected_error_includes_type_name(self):
        # A non-network error inside the request path (e.g. a decode bug)
        # should be wrapped with its type name, not a bare "Request failed".
        def side_effect(req, timeout=None):
            raise ValueError("surprise")

        with patch("urllib.request.urlopen", side_effect=side_effect), \
             patch.object(check_versions.time, "sleep"):
            with self.assertRaises(RuntimeError) as ctx:
                check_versions._make_request("https://example.com/api")
        self.assertIn("ValueError", str(ctx.exception))


class TestUpdateVersionInFileUnquoted(unittest.TestCase):
    """An unquoted pin is rewritten unquoted."""

    def test_unquoted_value_stays_unquoted(self):
        path = write_tmp_vars("foo_version: 1.2.3\nbar_version: 4.5.6\n")
        try:
            with patch.object(check_versions, "VARS_FILE", path):
                self.assertTrue(
                    check_versions.update_version_in_file("foo_version", "1.2.4")
                )
            out = path.read_text()
            self.assertIn("foo_version: 1.2.4", out)
            self.assertNotIn('foo_version: "1.2.4"', out)  # must stay unquoted
            self.assertIn("bar_version: 4.5.6", out)  # untouched
        finally:
            os.unlink(path)

    def test_prefix_collision_not_matched(self):
        """`redis_version` must not match `redis_exporter_version` (the trailing
        ':' in the startswith guard prevents the prefix collision)."""
        path = write_tmp_vars(
            'redis_version: "1.0"\nredis_exporter_version: "2.0"\n'
        )
        try:
            with patch.object(check_versions, "VARS_FILE", path):
                self.assertTrue(
                    check_versions.update_version_in_file("redis_version", "1.1")
                )
            out = path.read_text()
            self.assertIn('redis_version: "1.1"', out)
            self.assertIn('redis_exporter_version: "2.0"', out)  # untouched
        finally:
            os.unlink(path)


class TestReadCurrentVersions(unittest.TestCase):
    """read_current_versions parses all.yml by hand (no PyYAML); feed it a
    representative snippet — mix of quoted/unquoted top-level *_version keys,
    inline comments, and a helm_chart_versions block."""

    SNIPPET = (
        "# header comment\n"
        '\n'
        'authentik_version: "2026.2.2"  # Currently deployed\n'
        "debian_version: 13\n"
        "plex_version: 1.43.1.5266  # inline note\n"
        "unrelated_key: ignored\n"
        "helm_chart_versions:\n"
        '  traefik: "40.0.0"  # comment\n'
        "  cert_manager: v1.20.2\n"
        "some_other_top: value\n"
    )

    def test_parses_mixed_snippet(self):
        path = write_tmp_vars(self.SNIPPET)
        try:
            with patch.object(check_versions, "VARS_FILE", path):
                versions = check_versions.read_current_versions()
        finally:
            os.unlink(path)
        self.assertEqual(versions.get("authentik_version"), "2026.2.2")
        self.assertEqual(versions.get("debian_version"), "13")
        self.assertEqual(versions.get("plex_version"), "1.43.1.5266")
        self.assertEqual(versions.get("helm_chart_versions.traefik"), "40.0.0")
        self.assertEqual(versions.get("helm_chart_versions.cert_manager"), "v1.20.2")
        # Keys whose name has no '_version' substring are not collected.
        self.assertNotIn("unrelated_key", versions)
        self.assertNotIn("some_other_top", versions)

    def test_a_non_pin_key_containing_version_is_not_collected(self):
        """Only keys ENDING in `_version` are pins; `min_version_note` is not,
        and collecting it would red --check-coverage for a value with no
        upstream."""
        path = write_tmp_vars("min_version_note: hello\nk3s_version: v1.2.3\n")
        try:
            with patch.object(check_versions, "VARS_FILE", path):
                versions = check_versions.read_current_versions()
        finally:
            os.unlink(path)
        self.assertNotIn("min_version_note", versions)
        self.assertEqual(versions["k3s_version"], "v1.2.3")

    def test_top_level_after_helm_block_still_parsed(self):
        """A *_version key appearing AFTER the helm block (block exited) is
        still collected — guards the in_helm fall-through."""
        path = write_tmp_vars(
            "helm_chart_versions:\n"
            '  traefik: "40.0.0"\n'
            'gitlab_version: "17.0.0"\n'
        )
        try:
            with patch.object(check_versions, "VARS_FILE", path):
                versions = check_versions.read_current_versions()
        finally:
            os.unlink(path)
        self.assertEqual(versions.get("helm_chart_versions.traefik"), "40.0.0")
        self.assertEqual(versions.get("gitlab_version"), "17.0.0")

    def test_nested_version_key_is_not_a_pin(self):
        """Only column-0 keys are pins; a `*_version` nested under another
        mapping must not be read (nor later rewritten) as top level."""
        path = write_tmp_vars(
            "some_block:\n"
            '  nested_version: "2.0.0"\n'
            'gitlab_version: "17.0.0"\n'
        )
        try:
            with patch.object(check_versions, "VARS_FILE", path):
                versions = check_versions.read_current_versions()
        finally:
            os.unlink(path)
        self.assertNotIn("nested_version", versions)
        self.assertEqual(versions.get("gitlab_version"), "17.0.0")

    def test_reads_the_same_values_as_the_partner_pin_parser(self):
        """read_current_versions and stale_partner_pins share one line parser."""
        text = (
            'a_version: "1.2" # x\n'
            "b_version: '3.4'  # \"q\"\n"
            "lxc_template: debian-13.tar.zst  # pinned\n"
            "helm_chart_versions:\n"
            '  traefik: "40.0.0" # c\n'
            "  # commented: 9\n"
        )
        registry = [{"name": "lxc", "var_name": "lxc_template"}]
        path = write_tmp_vars(text)
        try:
            with patch.object(check_versions, "VARS_FILE", path), \
                 patch.object(check_versions, "SERVICE_REGISTRY", registry), \
                 patch.object(check_versions, "read_pinned_image_versions", return_value={}):
                versions = check_versions.read_current_versions()
        finally:
            os.unlink(path)
        pins = check_versions._top_level_pins(text)
        self.assertEqual(versions, {k: pins[k] for k in versions})
        self.assertEqual(versions, {
            "a_version": "1.2", "b_version": "3.4",
            "lxc_template": "debian-13.tar.zst",
            "helm_chart_versions.traefik": "40.0.0",
        })


@pytest.mark.parametrize("current,latest,notes,expected", [
    ("latest", "1.2.3", "", "'latest' resolves to 1.2.3"),
    ("latest", "1.2.3", "pre-existing note", "pre-existing note 'latest' resolves to 1.2.3"),
    ("1.2.0", "1.2.3", "keep me", "keep me"),
    ("latest", None, "", ""),
], ids=["latest-resolved", "latest-keeps-notes", "pinned-untouched", "latest-unresolved"])
def test_annotate_latest_resolution(current, latest, notes, expected):
    """Only a service tracking 'latest' shows what it resolved to."""
    result = check_versions.ServiceVersion(
        name="Some Service", category="dockerhub", current_version=current,
        latest_version=latest, var_name="some_version", notes=notes,
    )
    check_versions._annotate_latest_resolution(result, current)
    assert result.notes == expected


class TestCli(unittest.TestCase):
    def test_list_still_works(self):
        res = subprocess.run(
            [sys.executable, str(script_path), "--config", str(FIXTURE_CONFIG),
             "--repo-root", str(FIXTURE_REPO), "--list"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0)
        self.assertIn("Tracked services", res.stdout)


class TestFetchGithubReleaseTagFilter(unittest.TestCase):
    """The tag_filter branch of fetch_github_release.

    It paginates, skips drafts and prereleases, and returns the highest
    version rather than the newest by date.
    """

    # Mirrors the k3s registry entry.
    K3S_SVC = {
        "github_repo": "k3s-io/k3s",
        "version_prefix": "v",
        "strip_prefix": False,
        "tag_filter": r"^v\d+\.\d+\.\d+\+k3s\d+$",
    }

    @staticmethod
    def _page_dispatch(pages):
        """github_api side_effect: return the release list for the ?page=N."""
        def api(path):
            m = re.search(r"[?&]page=(\d+)", path)
            return pages.get(int(m.group(1)), [])
        return api

    def test_highest_not_newest_and_skip_is_load_bearing(self):
        # Page 1 (100 items -> paginates) leads with an older stable patch;
        # page 2 holds the true winner plus a higher prerelease and draft. The
        # winner must be the highest STABLE version across pages.
        page1 = [{"tag_name": "v1.34.9+k3s2"}] + [
            {"tag_name": f"nightly-{i}"} for i in range(99)  # non-matching filler
        ]
        page2 = [
            {"tag_name": "v1.35.3+k3s1"},
            {"tag_name": "v1.36.0+k3s1", "prerelease": True},
            {"tag_name": "v1.37.0+k3s1", "draft": True},
        ]
        pages = {1: page1, 2: page2}
        with patch.object(check_versions, "github_api",
                          side_effect=self._page_dispatch(pages)) as mock_api:
            result = check_versions.fetch_github_release(self.K3S_SVC)
        self.assertEqual(result, "v1.35.3+k3s1")  # prefix retained (strip_prefix False)
        # page 1 was full (100) -> page 2 fetched; page 2 <100 -> stop.
        self.assertEqual(mock_api.call_count, 2)

    def test_no_matching_release_raises(self):
        # A single page with only draft/prerelease/non-matching entries.
        page1 = [
            {"tag_name": "v1.36.0+k3s1", "prerelease": True},
            {"tag_name": "v1.37.0+k3s1", "draft": True},
            {"tag_name": "not-a-release"},
        ]
        with patch.object(check_versions, "github_api",
                          side_effect=self._page_dispatch({1: page1})):
            with self.assertRaises(RuntimeError) as ctx:
                check_versions.fetch_github_release(self.K3S_SVC)
        self.assertIn("No release matching", str(ctx.exception))

    def test_strip_prefix_applied_in_tag_filter_branch(self):
        # strip_prefix=True in the tag_filter branch must strip the version_prefix
        # from the winning tag (gluetun/mealie-style).
        svc = {
            "github_repo": "owner/repo",
            "version_prefix": "v",
            "strip_prefix": True,
            "tag_filter": r"^v\d+\.\d+\.\d+$",
        }
        page1 = [{"tag_name": "v1.2.3"}, {"tag_name": "v1.2.2"}]
        with patch.object(check_versions, "github_api",
                          side_effect=self._page_dispatch({1: page1})):
            self.assertEqual(check_versions.fetch_github_release(svc), "1.2.3")


class TestFetchGhcrVersion(unittest.TestCase):
    """fetch_ghcr_version: anonymous pull-token flow, tag_filter matching,
    highest-version selection."""

    @staticmethod
    def _dispatch(token_resp, tags_resp):
        def request(url, headers=None):
            if "/token" in url:
                return token_resp
            return tags_resp
        return request

    def test_standalone_filter_selects_highest_standalone(self):
        # The -standalone constraint keeps a bare X.Y.Z from ranking above its
        # -standalone twin.
        svc = {"ghcr_image": "thecfu/gluetun-exporter",
               "tag_filter": r"^\d+\.\d+\.\d+-standalone$"}
        tags = {"tags": ["1.2.3", "1.2.3-standalone", "1.3.0-standalone"]}
        with patch.object(check_versions, "_make_request",
                          side_effect=self._dispatch({"token": "x"}, tags)):
            self.assertEqual(check_versions.fetch_ghcr_version(svc), "1.3.0-standalone")

    def test_missing_token_raises(self):
        svc = {"ghcr_image": "thecfu/gluetun-exporter"}
        with patch.object(check_versions, "_make_request",
                          side_effect=self._dispatch({}, {"tags": ["1.0.0"]})):
            with self.assertRaises(RuntimeError):
                check_versions.fetch_ghcr_version(svc)

    def test_non_json_tags_raises(self):
        svc = {"ghcr_image": "thecfu/gluetun-exporter"}
        with patch.object(check_versions, "_make_request",
                          side_effect=self._dispatch({"token": "x"}, "<html>500</html>")):
            with self.assertRaises(RuntimeError):
                check_versions.fetch_ghcr_version(svc)


class TestVersionCache(unittest.TestCase):
    """_read_cache / _write_cache: round-trip, TTL expiry, and the corrupted-
    cache self-heal unlink."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.patcher = patch.object(check_versions, "CACHE_DIR", self.tmp)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_write_then_read_roundtrips(self):
        check_versions._write_cache("Some Service", "1.2.3")
        self.assertEqual(check_versions._read_cache("Some Service"), "1.2.3")

    def test_expired_entry_returns_none(self):
        cache_file = check_versions._cache_key("Old Service")
        cache_file.write_text(json.dumps({
            "version": "9.9.9",
            "timestamp": time.time() - check_versions.CACHE_TTL - 1,
            "service": "Old Service",
        }))
        self.assertIsNone(check_versions._read_cache("Old Service"))

    def test_corrupted_entry_is_removed(self):
        cache_file = check_versions._cache_key("Bad Service")
        cache_file.write_text("not json {{{")
        self.assertIsNone(check_versions._read_cache("Bad Service"))
        # Self-heal: the unreadable entry is unlinked so it can't wedge every run.
        self.assertFalse(cache_file.exists())


class TestFormatJson(unittest.TestCase):
    """format_json's summary contract: held and current-unreadable updates are
    excluded from updates_available and counted separately (version-check-ci.py
    keys its exit code off that distinction)."""

    def test_summary_counts_and_held_flag(self):
        SV = check_versions.ServiceVersion
        results = [
            SV(name="HeldChart", category="helm", current_version="0.15.3",
               latest_version="0.16.0", update_available=True,
               var_name="helm_chart_versions.held_chart", held=True),
            SV(name="Foo", category="helm", current_version="1.0",
               latest_version="1.1", update_available=True, var_name="foo_version"),
            SV(name="Bar", category="github", current_version="1.0",
               var_name="bar_version", error="boom"),
            SV(name="Baz", category="github", current_version="2.0",
               latest_version="2.0", update_available=False, var_name="baz_version"),
        ]
        data = json.loads(check_versions.format_json(results))
        self.assertEqual(data["summary"], {
            "total": 4,
            "up_to_date": 1,
            "updates_available": 1,   # only Foo; the held chart is excluded
            "updates_held": 1,
            "updates_current_unreadable": 0,
            "errors": 1,
        })
        held = next(s for s in data["services"] if s["name"] == "HeldChart")
        self.assertIs(held.get("held"), True)


class TestUnreadableCurrentPin(unittest.TestCase):
    """An entry whose current version cannot be read from this repo."""

    def _results(self):
        SV = check_versions.ServiceVersion
        return [
            SV(name="PR Agent", category="dockerhub", current_version="unknown",
               latest_version="0.39", update_available=True,
               var_name="pr_agent_version", unreadable_current=True,
               notes="pinned in another repository's CI template"),
            SV(name="Foo", category="helm", current_version="1.0",
               latest_version="1.1", update_available=True, var_name="foo_version"),
        ]

    def test_it_is_not_actionable(self):
        unreadable, actionable = self._results()
        self.assertFalse(check_versions.is_actionable(unreadable))
        self.assertTrue(check_versions.is_actionable(actionable))

    def test_json_separates_it_from_held_and_from_updates(self):
        data = json.loads(check_versions.format_json(self._results()))
        self.assertEqual(data["summary"]["updates_available"], 1)
        self.assertEqual(data["summary"]["updates_current_unreadable"], 1)
        self.assertEqual(data["summary"]["updates_held"], 0)
        entry = next(s for s in data["services"] if s["name"] == "PR Agent")
        self.assertIs(entry["unreadable_current"], True)
        self.assertIs(entry["update_available"], False)

    def test_report_names_the_status_and_still_prints_the_release(self):
        with patch.object(check_versions, "should_use_color", lambda: False):
            report = check_versions.format_table(self._results())
        self.assertIn("CURRENT UNREADABLE", report)
        self.assertIn("0.39", report)
        self.assertIn("pinned in another repository's CI template", report)

    def test_without_the_flag_the_same_entry_reports_an_update_forever(self):
        # The negative case the flag exists for: an unreadable pin otherwise
        # counts as actionable and flips the exit code on every run.
        unreadable, _ = self._results()
        unreadable.unreadable_current = False
        self.assertTrue(check_versions.is_actionable(unreadable))


class TestFormatTableUnknownCategory(unittest.TestCase):
    """A registry entry with an unrecognised category must still be rendered
    (under "Other") and still be counted in the summary."""

    def test_unknown_category_is_rendered_and_counted(self):
        SV = check_versions.ServiceVersion
        results = [
            SV(name="Typo", category="gihtub", current_version="1.0",
               var_name="typo_version", error="Unknown category: gihtub"),
            SV(name="Foo", category="helm", current_version="1.0",
               latest_version="1.1", update_available=True, var_name="foo_version"),
        ]
        out = check_versions.format_table(results)
        self.assertIn("Typo", out)
        self.assertIn("Other", out)
        self.assertIn("Errors:         1", out)
        self.assertIn("Updates:        1", out)


class TestCheckService(unittest.TestCase):
    """check_service — the function that decides update_available — exercised
    for real (never mocked): update detection on both the live-fetch and
    cache-hit paths, plus the RuntimeError-vs-typed-Exception error mapping."""

    SVC = {"name": "Fake GH", "var_name": "fake_version", "category": "github"}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.patcher = patch.object(check_versions, "CACHE_DIR", self.tmp)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_live_update_available_when_latest_greater(self):
        # use_cache=False so the patched fetcher is actually reached (check_service
        # reads the cache first and would short-circuit otherwise).
        with patch.object(check_versions, "fetch_github_release", return_value="2.0.0"):
            r = check_versions.check_service(self.SVC, {"fake_version": "1.0.0"}, use_cache=False)
        self.assertTrue(r.update_available)
        self.assertIsNone(r.error)

    def test_live_no_update_when_equal(self):
        with patch.object(check_versions, "fetch_github_release", return_value="1.0.0"):
            r = check_versions.check_service(self.SVC, {"fake_version": "1.0.0"}, use_cache=False)
        self.assertFalse(r.update_available)

    def test_current_latest_never_updates(self):
        with patch.object(check_versions, "fetch_github_release", return_value="2.0.0"):
            r = check_versions.check_service(self.SVC, {"fake_version": "latest"}, use_cache=False)
        self.assertFalse(r.update_available)

    def _cache(self, version, current):
        check_versions._write_cache(
            self.SVC["name"], version, check_versions.cache_fingerprint(self.SVC, current))

    def test_cache_hit_drives_update_available(self):
        self._cache("3.0.0", "2.0.0")
        # A cache hit must return before any fetch.
        with patch.object(check_versions, "fetch_github_release",
                          side_effect=AssertionError("must not fetch on cache hit")):
            r = check_versions.check_service(self.SVC, {"fake_version": "2.0.0"}, use_cache=True)
        self.assertEqual(r.latest_version, "3.0.0")
        self.assertTrue(r.update_available)

    def test_cache_hit_no_update_when_not_greater(self):
        self._cache("2.0.0", "2.0.0")
        with patch.object(check_versions, "fetch_github_release",
                          side_effect=AssertionError("must not fetch on cache hit")):
            r = check_versions.check_service(self.SVC, {"fake_version": "2.0.0"}, use_cache=True)
        self.assertFalse(r.update_available)

    def test_an_edited_registry_entry_misses_the_cache(self):
        self._cache("3.0.0", "2.0.0")
        edited = {**self.SVC, "tag_filter": r"^v2\."}
        with patch.object(check_versions, "fetch_github_release", return_value="2.5.0") as fetch:
            r = check_versions.check_service(edited, {"fake_version": "2.0.0"}, use_cache=True)
        fetch.assert_called_once()
        self.assertEqual(r.latest_version, "2.5.0")

    def test_a_moved_pin_misses_the_cache(self):
        """The window check runs again once the pin moves past the cached lookup."""
        self._cache("3.0.0", "2.0.0")
        with patch.object(check_versions, "fetch_github_release",
                          side_effect=RuntimeError("window ended before the pin")) as fetch:
            r = check_versions.check_service(self.SVC, {"fake_version": "4.0.0"}, use_cache=True)
        fetch.assert_called_once()
        self.assertEqual(r.error, "window ended before the pin")

    def test_two_ghcr_entries_for_one_package_keep_separate_caches(self):
        a = {"name": "Tool A", "var_name": "a_version", "category": "ghcr",
             "ghcr_image": "one/tool"}
        b = {"name": "Tool B", "var_name": "b_version", "category": "ghcr",
             "ghcr_image": "two/tool"}
        with patch.object(check_versions, "fetch_ghcr_version", return_value="1.0.0"):
            check_versions.check_service(a, {"a_version": "1.0.0"}, use_cache=True)
        with patch.object(check_versions, "fetch_ghcr_version", return_value="2.0.0") as fetch:
            r = check_versions.check_service(b, {"b_version": "1.0.0"}, use_cache=True)
        fetch.assert_called_once()
        self.assertEqual(r.latest_version, "2.0.0")
        self.assertEqual(r.source_url, "https://github.com/two/tool/pkgs/container/tool")
        self.assertTrue(check_versions._cache_key("Tool B").exists())
        self.assertFalse(check_versions._cache_key("tool").exists())

    def test_a_nested_ghcr_package_links_its_encoded_package_page(self):
        svc = {"name": "Nested", "var_name": "n_version", "category": "ghcr",
               "ghcr_image": "owner/repo/sub"}
        with patch.object(check_versions, "fetch_ghcr_version", return_value="1.0.0"):
            r = check_versions.check_service(svc, {"n_version": "1.0.0"}, use_cache=False)
        self.assertEqual(r.source_url,
                         "https://github.com/owner/repo/pkgs/container/repo%2Fsub")

    def test_runtimeerror_surfaced_verbatim(self):
        with patch.object(check_versions, "fetch_github_release",
                          side_effect=RuntimeError("upstream 500")):
            r = check_versions.check_service(self.SVC, {"fake_version": "1.0.0"}, use_cache=False)
        self.assertEqual(r.error, "upstream 500")
        self.assertNotIn("Unexpected", r.error)

    def test_generic_exception_is_type_tagged(self):
        with patch.object(check_versions, "fetch_github_release",
                          side_effect=ValueError("boom")):
            r = check_versions.check_service(self.SVC, {"fake_version": "1.0.0"}, use_cache=False)
        self.assertTrue(r.error.startswith("Unexpected ValueError"))


class TestMainExitCodes(unittest.TestCase):
    """The default run's exit-code contract: errors 2, actionable updates 1,
    clean or held-only 0. The CI job and version-check-ci.py reconcile
    against it."""

    def _run_main_with(self, results):
        with patch.object(check_versions, "check_all", return_value=results), \
             patch.object(check_versions, "read_current_versions", return_value={}), \
             patch.object(check_versions.sys, "argv", ["check-versions.py"]):
            with self.assertRaises(SystemExit) as cm:
                check_versions.main()
        return cm.exception.code

    def test_held_only_and_clean_exits_0(self):
        SV = check_versions.ServiceVersion
        held = SV(name="HeldChart", category="helm", current_version="0.15.3",
                  latest_version="0.16.0", update_available=True,
                  var_name="helm_chart_versions.held_chart", held=True)
        clean = SV(name="Foo", category="helm", current_version="1.0",
                   latest_version="1.0", var_name="foo_version")
        self.assertEqual(self._run_main_with([held, clean]), 0)

    def test_actionable_update_exits_1(self):
        SV = check_versions.ServiceVersion
        upd = SV(name="Foo", category="helm", current_version="1.0",
                 latest_version="1.1", update_available=True, var_name="foo_version")
        self.assertEqual(self._run_main_with([upd]), 1)

    def test_error_exits_2(self):
        SV = check_versions.ServiceVersion
        err = SV(name="Bar", category="github", current_version="1.0",
                 var_name="bar_version", error="boom")
        self.assertEqual(self._run_main_with([err]), 2)


class TestDockerhubPageSize(unittest.TestCase):
    """dockerhub_page_size is registry data: the shared fetcher must not know
    any consumer's image name."""

    def _capture(self):
        seen = []

        def fake_request(url, headers=None):
            seen.append(url)
            return {"results": [{"name": "1.0.0"}], "next": None}

        return seen, fake_request

    def test_default_page_size(self):
        seen, fake = self._capture()
        with patch.object(check_versions, "_make_request", fake):
            check_versions.fetch_dockerhub_version(
                {"docker_image": "library/postgres", "tag_regex": r"^\d+\.\d+\.\d+$"})
        self.assertIn("page_size=50", seen[0])

    def test_registry_page_size_reaches_the_request(self):
        seen, fake = self._capture()
        with patch.object(check_versions, "_make_request", fake):
            check_versions.fetch_dockerhub_version({
                "docker_image": "library/postgres",
                "tag_regex": r"^\d+\.\d+\.\d+$",
                "dockerhub_page_size": 100,
            })
        self.assertIn("page_size=100", seen[0])


class TestRegistrySourceUrlOverride(unittest.TestCase):
    """A registry `source_url` wins over the URL derived from the image name."""

    def test_override_wins(self):
        svc = next(s for s in check_versions.SERVICE_REGISTRY
                   if s["var_name"] == "registry_cache_version")
        with patch.object(check_versions, "fetch_dockerhub_version", return_value="3.1.1"):
            result = check_versions.check_service(svc, {"registry_cache_version": "3.1.1"},
                                                  use_cache=False)
        self.assertEqual(result.source_url, "https://hub.docker.com/_/registry")

    def test_without_override_the_derived_url_is_used(self):
        svc = {k: v for k, v in next(
            s for s in check_versions.SERVICE_REGISTRY
            if s["var_name"] == "registry_cache_version").items() if k != "source_url"}
        with patch.object(check_versions, "fetch_dockerhub_version", return_value="3.1.1"):
            result = check_versions.check_service(svc, {"registry_cache_version": "3.1.1"},
                                                  use_cache=False)
        self.assertEqual(result.source_url, "https://hub.docker.com/r/library/registry/tags")


class TestFilterCombinationThatMatchesNothing(unittest.TestCase):
    """--service X --category Y with an empty intersection is an operator error:
    one line and exit 2, never a traceback and never exit 1 ("updates available")."""

    def test_main_exits_2_with_one_line(self):
        with patch.object(check_versions, "read_current_versions", return_value={}), \
             patch.object(check_versions.sys, "argv",
                          ["check-versions.py", "--service", "k3s", "--category", "helm"]):
            with self.assertRaises(SystemExit) as cm:
                check_versions.main()
        self.assertEqual(cm.exception.code, 2)

    def test_cli_prints_the_message_not_a_traceback(self):
        res = subprocess.run(
            [sys.executable, str(script_path), "--config", str(FIXTURE_CONFIG),
             "--repo-root", str(FIXTURE_REPO), "--service", "k3s", "--category", "helm"],
            capture_output=True, text=True,
        )
        self.assertEqual(res.returncode, 2)
        self.assertIn("no services match category", res.stderr)
        self.assertNotIn("Traceback", res.stderr)


class TestClearCache(unittest.TestCase):
    """--clear-cache empties the cache; an unexpected entry must not abort it."""

    def _run_clear(self, cache_dir):
        real_load = check_versions.load_config

        def load_then_point(*args, **kwargs):
            # main() re-resolves the consumer config, which rebinds CACHE_DIR.
            cfg = real_load(*args, **kwargs)
            check_versions.CACHE_DIR = cache_dir
            return cfg

        with patch.object(check_versions, "CACHE_DIR", cache_dir), \
             patch.object(check_versions, "load_config", load_then_point), \
             patch.object(check_versions.sys, "argv", ["check-versions.py", "--clear-cache"]):
            with self.assertRaises(SystemExit) as cm:
                check_versions.main()
        return cm.exception.code

    def test_a_stray_directory_does_not_abort(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            (cache / "stray").mkdir(parents=True)
            (cache / "svc.json").write_text("{}")
            self.assertEqual(self._run_clear(cache), 0)
            self.assertFalse(cache.exists())

    def test_absent_cache_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self._run_clear(Path(tmp) / "absent"), 0)


class TestCoupledPins(unittest.TestCase):
    """A coupled pin is written and its partner edit demanded loudly; the
    partner value itself is a hand edit --check-partner-pins then enforces."""

    def _results(self):
        SV = check_versions.ServiceVersion
        coupled = SV(name="Coupled Tool", category="github", current_version="1.0.0",
                     latest_version="1.1.0", update_available=True,
                     var_name="coupled_tool_version",
                     notes="recompute coupled_tool_checksum from the release asset",
                     coupled_vars=["coupled_tool_checksum"])
        plain = SV(name="Foo", category="helm", current_version="1.0",
                   latest_version="1.1", update_available=True, var_name="foo_version")
        return coupled, plain

    def _update_all(self):
        coupled, plain = self._results()
        with patch.object(check_versions, "check_all", return_value=[coupled, plain]), \
             patch.object(check_versions, "read_current_versions", return_value={}), \
             patch.object(check_versions, "get_deploy_command", return_value="deploy"), \
             patch.object(check_versions, "update_version_in_file", return_value=True) as muf, \
             patch.object(check_versions.sys, "argv", ["check-versions.py", "--update-all"]):
            with self.assertRaises(SystemExit) as cm:
                check_versions.main()
        return muf, cm.exception.code

    def test_update_all_writes_the_coupled_pin_too(self):
        muf, code = self._update_all()
        written = [call.args[0] for call in muf.call_args_list]
        self.assertIn("foo_version", written)
        self.assertIn("coupled_tool_version", written)
        self.assertEqual(code, 0)

    def test_update_all_names_the_partner_and_the_note(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self._update_all()
        out = buf.getvalue()
        self.assertIn("PAIRED-EDIT-REQUIRED", out)
        self.assertIn("coupled_tool_checksum", out)
        self.assertIn("recompute coupled_tool_checksum", out)

    def test_a_coupled_entry_is_still_an_available_update(self):
        coupled, plain = self._results()
        data = json.loads(check_versions.format_json([coupled, plain]))
        by_name = {s["name"]: s for s in data["services"]}
        self.assertTrue(by_name["Coupled Tool"]["update_available"])
        self.assertEqual(by_name["Coupled Tool"]["coupled_vars"], ["coupled_tool_checksum"])
        self.assertEqual(data["summary"]["updates_available"], 2)

    def test_single_update_writes_and_demands_the_paired_edit(self):
        coupled, _ = self._results()
        buf = io.StringIO()
        with patch.object(check_versions, "check_service", return_value=coupled), \
             patch.object(check_versions, "read_current_versions", return_value={}), \
             patch.object(check_versions, "get_deploy_command", return_value="deploy"), \
             patch.object(check_versions, "update_version_in_file", return_value=True) as muf, \
             patch.object(check_versions.sys, "argv",
                          ["check-versions.py", "--update", "coupled_tool_version"]):
            with contextlib.redirect_stdout(buf):
                with self.assertRaises(SystemExit) as cm:
                    check_versions.main()
        muf.assert_called_once()
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("PAIRED EDIT REQUIRED", buf.getvalue())
        self.assertIn("coupled_tool_checksum", buf.getvalue())

    def test_a_registry_without_coupled_vars_still_loads(self):
        svc = {"name": "Plain", "var_name": "plain_version", "category": "manual"}
        result = check_versions.check_service(svc, {"plain_version": "1"})
        self.assertEqual(result.coupled_vars, [])


class TestCompanionPins(unittest.TestCase):
    """A lockstep triplet (version, `-r1` revision, tag SHA) moves in one edit."""

    def _result(self, **overrides):
        SV = check_versions.ServiceVersion
        base = dict(
            name="Tool", category="github", current_version="2026.8.1",
            latest_version="2026.9.1", update_available=True,
            var_name="tool_version",
            revision_var="tool_image_version",
            sha_var={"var": "tool_git_sha", "repo": "https://example.test/tool.git",
                     "ref": "refs/tags/v{version}"},
        )
        base.update(overrides)
        return SV(**base)

    LS_REMOTE = (
        "1111111111111111111111111111111111111111\trefs/tags/v2026.9.1\n"
        "2222222222222222222222222222222222222222\trefs/tags/v2026.9.1^{}\n"
    )

    def _ls_remote(self, stdout, returncode=0):
        return patch.object(
            check_versions.subprocess, "run",
            return_value=SimpleNamespace(returncode=returncode, stdout=stdout, stderr=""),
        )

    def test_the_peeled_commit_is_taken_not_the_tag_object(self):
        with self._ls_remote(self.LS_REMOTE):
            pairs = check_versions.companion_pins(self._result())
        self.assertEqual(pairs, [
            ("tool_image_version", "2026.9.1-r1"),
            ("tool_git_sha", "2" * 40),
        ])

    def test_ls_remote_is_bounded_and_never_prompts(self):
        with self._ls_remote(self.LS_REMOTE) as run:
            check_versions.resolve_tag_sha("https://example.test/tool.git", "refs/tags/v2026.9.1")
        self.assertEqual(run.call_args.kwargs["timeout"], check_versions.GIT_TIMEOUT)
        self.assertEqual(run.call_args.kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")

    def test_an_ls_remote_timeout_raises(self):
        timeout = subprocess.TimeoutExpired(cmd="git", timeout=1)
        with patch.object(check_versions.subprocess, "run", side_effect=timeout):
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                check_versions.resolve_tag_sha("https://example.test/tool.git", "refs/tags/v1")

    def test_the_query_keeps_the_peel_visible(self):
        """`git ls-remote <repo> refs/tags/<tag>` alone hides the `^{}` line."""
        with self._ls_remote(self.LS_REMOTE) as run:
            check_versions.resolve_tag_sha(
                "https://example.test/tool.git", "refs/tags/v2026.9.1"
            )
        self.assertEqual(run.call_args.args[0][-1], "refs/tags/v2026.9.1*")

    def test_a_lightweight_tag_falls_back_to_the_plain_line(self):
        plain = "3333333333333333333333333333333333333333\trefs/tags/v2026.9.1\n"
        with self._ls_remote(plain):
            pairs = check_versions.companion_pins(self._result())
        self.assertEqual(pairs[1], ("tool_git_sha", "3" * 40))

    def test_an_unresolvable_ref_raises_rather_than_writing_an_empty_sha(self):
        with self._ls_remote(""):
            with self.assertRaises(RuntimeError) as ctx:
                check_versions.companion_pins(self._result())
        self.assertIn("resolves to no object", str(ctx.exception))

    def test_a_failed_ls_remote_raises(self):
        with self._ls_remote("", returncode=128):
            with self.assertRaises(RuntimeError):
                check_versions.companion_pins(self._result())

    def test_a_sha_var_without_a_repo_raises(self):
        result = self._result(sha_var={"var": "tool_git_sha"})
        with self.assertRaises(RuntimeError) as ctx:
            check_versions.companion_pins(result)
        self.assertIn("no `repo`", str(ctx.exception))

    def test_an_entry_with_neither_key_has_no_companions(self):
        result = self._result(revision_var="", sha_var={})
        self.assertEqual(check_versions.companion_pins(result), [])

    def test_the_triplet_is_written_together(self):
        with self._ls_remote(self.LS_REMOTE), \
             patch.object(check_versions, "update_version_in_file", return_value=True) as muf:
            written = check_versions.write_companion_pins(self._result())
        self.assertEqual(written, ["tool_image_version", "tool_git_sha"])
        self.assertEqual([c.args for c in muf.call_args_list], [
            ("tool_image_version", "2026.9.1-r1"),
            ("tool_git_sha", "2" * 40),
        ])

    def test_a_companion_pin_missing_from_the_vars_file_raises(self):
        with self._ls_remote(self.LS_REMOTE), \
             patch.object(check_versions, "update_version_in_file", return_value=False):
            with self.assertRaises(RuntimeError) as ctx:
                check_versions.write_companion_pins(self._result())
        self.assertIn("tool_image_version", str(ctx.exception))

    def test_a_companion_pin_is_not_reported_as_untracked(self):
        registry = [{
            "name": "Tool", "var_name": "tool_version", "category": "github",
            "revision_var": "tool_image_version",
            "sha_var": {"var": "tool_git_sha", "repo": "https://example.test/tool.git"},
        }]
        versions = {
            "tool_version": "1", "tool_image_version": "1-r1",
            "tool_git_sha": "a" * 40, "loose_version": "2",
        }
        with patch.object(check_versions, "SERVICE_REGISTRY", registry), \
             patch.object(check_versions, "read_current_versions", return_value=versions):
            self.assertEqual(check_versions.missing_registry_entries(), ["loose_version"])


class TestStalePartnerPins(unittest.TestCase):
    """--check-partner-pins reds an MR whose coupled pin moved alone."""

    REGISTRY = [{
        "name": "Coupled Tool",
        "var_name": "coupled_tool_version",
        "category": "github",
        "coupled_vars": ["coupled_tool_checksum"],
    }]

    def setUp(self):
        self._saved = check_versions.SERVICE_REGISTRY
        check_versions.SERVICE_REGISTRY = self.REGISTRY

    def tearDown(self):
        check_versions.SERVICE_REGISTRY = self._saved

    BASE = 'coupled_tool_version: "1.0.0"\ncoupled_tool_checksum: "aaa"\nfoo_version: "1"\n'

    def test_a_moved_pin_with_an_unmoved_partner_is_stale(self):
        head = self.BASE.replace('"1.0.0"', '"1.1.0"')
        stale = check_versions.stale_partner_pins(self.BASE, head)
        self.assertEqual(len(stale), 1)
        self.assertIn("coupled_tool_checksum", stale[0])

    CHECKSUMMED = [{
        "name": "Coupled Tool",
        "var_name": "coupled_tool_version",
        "category": "github",
        "coupled_vars": ["coupled_tool_checksum"],
        "checksum_var": "coupled_tool_checksum",
        "checksum_url": "https://example.test/{version}/install.sh",
    }]

    def test_an_unmoved_checksum_that_still_hashes_the_artefact_is_not_stale(self):
        check_versions.SERVICE_REGISTRY = self.CHECKSUMMED
        head = self.BASE.replace('"1.0.0"', '"1.1.0"')
        self.assertEqual(check_versions.stale_partner_pins(self.BASE, head, lambda entry: True), [])
        stale = check_versions.stale_partner_pins(self.BASE, head, lambda entry: False)
        self.assertEqual(len(stale), 1)
        self.assertIn("coupled_tool_checksum", stale[0])

    def test_a_coupled_pin_whose_partner_is_missing_is_reported(self):
        head = self.BASE.replace('coupled_tool_checksum: "aaa"\n', "")
        stale = check_versions.stale_partner_pins(self.BASE, head)
        self.assertEqual(len(stale), 1)
        self.assertIn("coupled_tool_checksum not found", stale[0])

    def test_a_renamed_coupled_pin_is_reported(self):
        head = self.BASE.replace("coupled_tool_version:", "renamed_tool_version:")
        stale = check_versions.stale_partner_pins(self.BASE, head)
        self.assertEqual(len(stale), 1)
        self.assertIn("is not a pin in the vars file", stale[0])

    OPERATOR = [{
        "name": "Operator", "var_name": "operator_version", "category": "github",
        "coupled_vars": ["helm_chart_versions.operator_crds"],
    }]

    def test_a_helm_chart_partner_is_read(self):
        base = 'operator_version: "1.0"\nhelm_chart_versions:\n  operator_crds: "5.0"\n'
        with patch.object(check_versions, "SERVICE_REGISTRY", self.OPERATOR):
            moved_alone = check_versions.stale_partner_pins(
                base, base.replace('"1.0"', '"1.1"'))
            moved_both = check_versions.stale_partner_pins(
                base, base.replace('"1.0"', '"1.1"').replace('"5.0"', '"5.1"'))
        self.assertEqual(len(moved_alone), 1)
        self.assertIn("helm_chart_versions.operator_crds", moved_alone[0])
        self.assertEqual(moved_both, [])

    def test_a_partner_nested_under_another_block_is_not_the_helm_pin(self):
        text = ('operator_version: "1.0"\nother:\n  operator_crds: "5.0"\n'
                'helm_chart_versions:\n  unrelated: "1.0"\n')
        with patch.object(check_versions, "SERVICE_REGISTRY", self.OPERATOR):
            stale = check_versions.stale_partner_pins(text, text)
        self.assertEqual(len(stale), 1)
        self.assertIn("helm_chart_versions.operator_crds not found", stale[0])

    def test_a_commented_helm_entry_is_not_the_partner(self):
        text = ('operator_version: "1.0"\nhelm_chart_versions:\n'
                '  # operator_crds: "5.0"\n  unrelated: "1.0"\n')
        pins = check_versions._top_level_pins(text)
        self.assertEqual(
            set(pins), {"operator_version", "helm_chart_versions",
                        "helm_chart_versions.unrelated"})
        with patch.object(check_versions, "SERVICE_REGISTRY", self.OPERATOR):
            stale = check_versions.stale_partner_pins(text, text)
        self.assertEqual(len(stale), 1)
        self.assertIn("helm_chart_versions.operator_crds not found", stale[0])

    def test_moving_both_is_clean(self):
        head = self.BASE.replace('"1.0.0"', '"1.1.0"').replace('"aaa"', '"bbb"')
        self.assertEqual(check_versions.stale_partner_pins(self.BASE, head), [])

    def test_an_untouched_file_is_clean(self):
        self.assertEqual(check_versions.stale_partner_pins(self.BASE, self.BASE), [])

    def test_an_unrelated_pin_moving_alone_is_clean(self):
        head = self.BASE.replace('foo_version: "1"', 'foo_version: "2"')
        self.assertEqual(check_versions.stale_partner_pins(self.BASE, head), [])

    def test_indented_keys_are_not_read_as_top_level_pins(self):
        head = self.BASE.replace('"1.0.0"', '"1.1.0"') + "  coupled_tool_checksum: bbb\n"
        stale = check_versions.stale_partner_pins(self.BASE, head)
        self.assertEqual(len(stale), 1)

    def test_an_inline_comment_is_not_part_of_the_value(self):
        head = 'coupled_tool_version: "1.0.0"  # held\ncoupled_tool_checksum: "aaa"\n'
        self.assertEqual(check_versions.stale_partner_pins(self.BASE, head), [])


class TestRunCheckPartnerPins(unittest.TestCase):
    """The CLI driver that turns stale_partner_pins() into a gate."""

    REGISTRY = TestStalePartnerPins.REGISTRY
    BASE = TestStalePartnerPins.BASE

    def setUp(self):
        self._saved = check_versions.SERVICE_REGISTRY
        check_versions.SERVICE_REGISTRY = self.REGISTRY
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.vars_file = self.root / "vars.yml"
        self.vars_file.write_text(self.BASE)
        self._git("init", "-q", "-b", "main")
        self._git("add", "-A")
        self._git("-c", "user.email=gate@example.invalid", "-c", "user.name=gate",
                  "commit", "-qm", "base")

    def tearDown(self):
        check_versions.SERVICE_REGISTRY = self._saved
        self._tmp.cleanup()

    def _git(self, *args):
        env = dict(os.environ)
        env["GIT_CONFIG_GLOBAL"] = str(self.root / "gitconfig")
        env["GIT_CONFIG_SYSTEM"] = str(self.root / "gitconfig")
        subprocess.run(["git", "-C", str(self.root), *args], check=True,
                       capture_output=True, env=env)

    @contextlib.contextmanager
    def _driver(self):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(check_versions, "REPO_ROOT", self.root), \
             patch.object(check_versions, "VARS_FILE", self.vars_file), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
             self.assertRaises(SystemExit) as raised:
            yield raised, out, err

    def test_a_pin_moved_without_its_partner_exits_one(self):
        self.vars_file.write_text(self.BASE.replace('"1.0.0"', '"1.1.0"'))
        with self._driver() as (raised, out, err):
            check_versions._run_check_partner_pins("HEAD")
        self.assertEqual(raised.exception.code, 1)
        self.assertIn("coupled_tool_checksum", err.getvalue())

    def test_moving_both_halves_exits_zero(self):
        self.vars_file.write_text(
            self.BASE.replace('"1.0.0"', '"1.1.0"').replace('"aaa"', '"bbb"')
        )
        with self._driver() as (raised, out, err):
            check_versions._run_check_partner_pins("HEAD")
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("No stale paired pins", out.getvalue())

    def test_an_unreadable_base_ref_is_an_operator_error(self):
        with self._driver() as (raised, out, err):
            check_versions._run_check_partner_pins("no-such-ref")
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("cannot read", err.getvalue())


class TestReadRegistry(unittest.TestCase):
    """The public wrapper the sibling gates read the registry through."""

    def test_a_mapping_registry_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            path.write_text('{"services": []}', encoding="utf-8")
            self.assertEqual(check_versions.read_registry(path), {"services": []})

    def test_a_non_mapping_registry_raises_value_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            path.write_text("[1, 2]", encoding="utf-8")
            with self.assertRaises(ValueError):
                check_versions.read_registry(path)

    def test_an_unsupported_suffix_raises_value_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.yaml"
            path.write_text("services: []\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                check_versions.read_registry(path)


class TestOperatorErrorsExit2(unittest.TestCase):
    """A broken checker exits 2, never 1, which the CI job reads as "updates available"."""

    def _run(self, config, repo_root, *args):
        return subprocess.run(
            [sys.executable, str(script_path), "--config", str(config),
             "--repo-root", str(repo_root), *args],
            capture_output=True, text=True,
        )

    def test_divergent_multifile_pins_exit_2(self):
        res = self._run(FIXTURE_DIR / "registry-divergent.py", FIXTURE_REPO)
        self.assertEqual(res.returncode, 2, res.stderr)
        self.assertIn("pins diverge", res.stderr)
        self.assertNotIn("Traceback", res.stderr)

    def test_a_missing_vars_file_exits_2(self):
        with tempfile.TemporaryDirectory() as d:
            config = Path(d) / "registry.json"
            config.write_text(json.dumps({
                "vars_file": "absent.yml",
                "services": [{"name": "X", "var_name": "x_version", "category": "manual"}],
            }))
            res = self._run(config, d)
        self.assertEqual(res.returncode, 2, res.stderr)
        self.assertIn("FileNotFoundError", res.stderr)

    def test_a_service_without_a_category_exits_2(self):
        with tempfile.TemporaryDirectory() as d:
            config = Path(d) / "registry.json"
            config.write_text(json.dumps({"services": [{"name": "X", "var_name": "x_version"}]}))
            res = self._run(config, d)
        self.assertEqual(res.returncode, 2, res.stderr)
        self.assertIn("needs a `category`", res.stderr)

    def test_a_service_filter_matching_nothing_exits_2(self):
        res = self._run(FIXTURE_CONFIG, FIXTURE_REPO, "--service", "no-such-service")
        self.assertEqual(res.returncode, 2)
        self.assertIn("No services matching", res.stderr)

    def test_an_unexpected_exception_exits_2(self):
        err = io.StringIO()
        with patch.object(check_versions, "check_all", side_effect=KeyError("category")), \
             patch.object(check_versions.sys, "argv", ["check-versions.py"]), \
             contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as cm:
                check_versions.main()
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("KeyError", err.getvalue())


class TestSummaryBuckets(unittest.TestCase):
    """Each result counts in exactly one bucket, the same in the table and JSON."""

    def _results(self):
        SV = check_versions.ServiceVersion
        return [
            SV(name="HeldAndErrored", category="helm", current_version="1",
               update_available=True, held=True, var_name="a_version", error="boom"),
            SV(name="HeldAndUnreadable", category="helm", current_version="unknown",
               latest_version="2", update_available=True, held=True,
               unreadable_current=True, var_name="b_version"),
            SV(name="Current", category="helm", current_version="1",
               latest_version="1", var_name="c_version"),
        ]

    def test_json_buckets_partition_the_results(self):
        summary = json.loads(check_versions.format_json(self._results()))["summary"]
        self.assertEqual(summary, {
            "total": 3, "up_to_date": 1, "updates_available": 0,
            "updates_held": 0, "updates_current_unreadable": 1, "errors": 1,
        })

    def test_the_table_agrees_with_the_json(self):
        with patch.object(check_versions, "should_use_color", lambda: False):
            report = check_versions.format_table(self._results())
        self.assertIn("Up to date:     1", report)
        self.assertIn("Pin elsewhere:  1", report)
        self.assertIn("Errors:         1", report)
        self.assertNotIn("Held:", report)


class TestDigestPinnedUpdates(unittest.TestCase):
    """A digest-pinned update is a manual edit, never a write failure."""

    def _pinned(self):
        return check_versions.ServiceVersion(
            name="PR Agent", category="dockerhub", current_version="0.30",
            latest_version="0.31", update_available=True, var_name="pr_agent_version")

    def test_update_all_lists_it_and_exits_0(self):
        out = io.StringIO()
        with patch.object(check_versions, "check_all", return_value=[self._pinned()]), \
             patch.object(check_versions.sys, "argv", ["check-versions.py", "--update-all"]), \
             contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as cm:
                check_versions.main()
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("digest-pinned update(s) need a manual edit", out.getvalue())
        self.assertIn("in .gitlab-ci.yml", out.getvalue())
        self.assertNotIn("Could not find", out.getvalue())
        self.assertNotIn("All services are up to date!", out.getvalue())

    def test_single_update_prints_the_manual_step_and_exits_0(self):
        out = io.StringIO()
        with patch.object(check_versions, "check_service", return_value=self._pinned()), \
             patch.object(check_versions.sys, "argv",
                          ["check-versions.py", "--update", "pr_agent_version"]), \
             contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as cm:
                check_versions.main()
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("digest-pinned in .gitlab-ci.yml", out.getvalue())

    def test_the_location_follows_the_consumer_alias(self):
        aliases = {"ci": check_versions.REPO_ROOT / "ci/pipeline.yml"}
        with patch.object(check_versions, "VERSION_FILE_ALIASES", aliases):
            self.assertEqual(check_versions.version_file_display("ci"), "ci/pipeline.yml")
            self.assertEqual(check_versions.version_file_display(["a.yml", "b.yml"]), "a.yml, b.yml")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
