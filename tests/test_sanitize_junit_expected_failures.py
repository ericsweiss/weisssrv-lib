"""sanitize-junit-expected-failures.py downgrades only declared junit failures."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from script_loader import load_script

sanitize_junit = load_script("sanitize-junit-expected-failures.py", register=True)


JUNIT = """<?xml version='1.0' encoding='utf-8'?>
<testsuites failures="2" errors="0" tests="3">
  <testsuite name="molecule" failures="2" errors="0" tests="3">
    <testcase name="[host] Converge: role : Fail if required storage pool does not exist">
      <failure message="boom">guard fired as designed</failure>
    </testcase>
    <testcase name="[host] Converge: role : A genuinely broken task">
      <failure message="real">unexpected</failure>
    </testcase>
    <testcase name="[host] Converge: role : A passing task"/>
  </testsuite>
</testsuites>
"""


def _write(tmp_path: Path, content: str = JUNIT) -> Path:
    p = tmp_path / "junit" / "run.xml"
    p.parent.mkdir()
    p.write_text(content)
    return p


def _expectations(tmp_path: Path, lines: list[str]) -> Path:
    p = tmp_path / "expected-junit-failures.txt"
    p.write_text("# comment\n\n" + "\n".join(lines) + "\n")
    return p


class TestSanitize:
    def test_downgrades_declared_failure_only(self, tmp_path):
        xml = _write(tmp_path)
        downgraded, undeclared = sanitize_junit.sanitize_file(
            xml, ["Fail if required storage pool does not exist"]
        )
        assert downgraded == 1
        assert undeclared == ["[host] Converge: role : A genuinely broken task"]
        root = ET.parse(xml).getroot()
        cases = {tc.get("name"): tc for tc in root.iter("testcase")}
        declared = cases["[host] Converge: role : Fail if required storage pool does not exist"]
        assert declared.find("failure") is None
        assert "expected negative-path failure" in declared.find("system-out").text
        broken = cases["[host] Converge: role : A genuinely broken task"]
        assert broken.find("failure") is not None

    def test_suite_counters_updated(self, tmp_path):
        xml = _write(tmp_path)
        sanitize_junit.sanitize_file(xml, ["Fail if required storage pool"])
        root = ET.parse(xml).getroot()
        assert root.find("testsuite").get("failures") == "1"
        # Aggregate <testsuites> attributes must track too — some consumers
        # read the root counts in preference to per-suite ones.
        assert root.get("failures") == "1"

    def test_no_expectations_is_noop(self, tmp_path):
        xml = _write(tmp_path)
        before = xml.read_text()
        rc = sanitize_junit.main(
            ["--junit-dir", str(xml.parent), "--expectations", str(tmp_path / "absent.txt")]
        )
        assert rc == 0
        assert xml.read_text() == before

    def test_main_downgrades_via_declaration_file(self, tmp_path, capsys):
        xml = _write(tmp_path)
        exp = _expectations(tmp_path, ["Fail if required storage pool does not exist"])
        rc = sanitize_junit.main(
            ["--junit-dir", str(xml.parent), "--expectations", str(exp)]
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "downgraded 1" in out
        assert "undeclared junit failure" in out  # the genuinely broken task warns

    def test_refuses_dtd_bearing_xml(self, tmp_path):
        evil = "<?xml version='1.0'?><!DOCTYPE x [<!ENTITY e 'x'>]><testsuites/>"
        xml = _write(tmp_path, evil)
        with pytest.raises(ValueError, match="DTD/entity"):
            sanitize_junit.sanitize_file(xml, ["anything"])

    def test_load_expectations_skips_comments_and_blanks(self, tmp_path):
        exp = _expectations(tmp_path, ["one", "two"])
        assert sanitize_junit.load_expectations(exp) == [("one", 1), ("two", 1)]
        assert sanitize_junit.load_expectations(tmp_path / "nope.txt") == []

    def test_load_expectations_reads_a_declared_count(self, tmp_path):
        exp = _expectations(tmp_path, ["one ::3", "two ::x"])
        assert sanitize_junit.load_expectations(exp) == [("one", 3), ("two ::x", 1)]


class TestStrict:
    """A renamed or deleted guard leaves its declaration behind, and the negative
    path silently stops being exercised."""

    def test_strict_fails_on_an_unobserved_expectation(self, tmp_path, capsys):
        xml = _write(tmp_path)
        exp = _expectations(
            tmp_path,
            ["Fail if required storage pool does not exist", "A guard that was renamed"],
        )
        rc = sanitize_junit.main(
            ["--junit-dir", str(xml.parent), "--expectations", str(exp), "--strict"]
        )
        assert rc == 1
        err = capsys.readouterr().err
        assert "never observed: A guard that was renamed" in err
        assert "Fail if required storage pool does not exist" not in err

    def test_strict_passes_when_every_expectation_fired(self, tmp_path):
        xml = _write(tmp_path)
        exp = _expectations(tmp_path, ["Fail if required storage pool does not exist"])
        rc = sanitize_junit.main(
            ["--junit-dir", str(xml.parent), "--expectations", str(exp), "--strict"]
        )
        assert rc == 0

    def test_non_strict_only_warns(self, tmp_path, capsys):
        xml = _write(tmp_path)
        exp = _expectations(tmp_path, ["A guard that was renamed"])
        rc = sanitize_junit.main(
            ["--junit-dir", str(xml.parent), "--expectations", str(exp)]
        )
        assert rc == 0
        assert "never observed" in capsys.readouterr().out


TWO_HITS = """<?xml version='1.0' encoding='utf-8'?>
<testsuites failures="2" errors="0" tests="2">
  <testsuite name="molecule" failures="2" errors="0" tests="2">
    <testcase name="[host] Converge: role : Attach additional disks to VM">
      <failure message="boom">negative path</failure>
    </testcase>
    <testcase name="[host] Verify: role : Attach additional disks to VM again">
      <failure message="real">a genuine failure of the same task</failure>
    </testcase>
  </testsuite>
</testsuites>
"""


class TestOverBroad:
    """One declaration must not downgrade every testcase whose name contains it."""

    def test_strict_fails_when_a_pattern_matches_more_than_declared(
        self, tmp_path, capsys
    ):
        xml = _write(tmp_path, TWO_HITS)
        exp = _expectations(tmp_path, ["Attach additional disks to VM"])
        rc = sanitize_junit.main(
            ["--junit-dir", str(xml.parent), "--expectations", str(exp), "--strict"]
        )
        assert rc == 1
        assert "matched 2 testcases, declared 1" in capsys.readouterr().err

    def test_strict_passes_when_the_count_is_declared(self, tmp_path):
        xml = _write(tmp_path, TWO_HITS)
        exp = _expectations(tmp_path, ["Attach additional disks to VM ::2"])
        rc = sanitize_junit.main(
            ["--junit-dir", str(xml.parent), "--expectations", str(exp), "--strict"]
        )
        assert rc == 0

    def test_non_strict_only_warns(self, tmp_path, capsys):
        xml = _write(tmp_path, TWO_HITS)
        exp = _expectations(tmp_path, ["Attach additional disks to VM"])
        rc = sanitize_junit.main(
            ["--junit-dir", str(xml.parent), "--expectations", str(exp)]
        )
        assert rc == 0
        assert "matched 2 testcases" in capsys.readouterr().out


ONE_HIT = """<?xml version='1.0' encoding='utf-8'?>
<testsuites failures="1" errors="0" tests="1">
  <testsuite name="molecule" failures="1" errors="0" tests="1">
    <testcase name="[host] Converge: role : Attach additional disks to VM">
      <failure message="boom">negative path</failure>
    </testcase>
  </testsuite>
</testsuites>
"""

# Two testcases, ONE task name: the junit callback writes one per host, so this
# is the same guard firing twice rather than an over-broad pattern.
SAME_NAME_TWICE = """<?xml version='1.0' encoding='utf-8'?>
<testsuites failures="2" errors="0" tests="2">
  <testsuite name="molecule" failures="2" errors="0" tests="2">
    <testcase name="[host-a] Converge: role : Attach additional disks to VM">
      <failure message="boom">the negative case, as designed</failure>
    </testcase>
    <testcase name="[host-b] Converge: role : Attach additional disks to VM">
      <failure message="real">a genuine failure of the same guard on another host</failure>
    </testcase>
  </testsuite>
</testsuites>
"""


class TestUnderCount:
    """Under --strict the count is exact, so a shortfall fails like an excess."""

    def test_strict_fails_when_fewer_hit_than_declared(self, tmp_path, capsys):
        xml = _write(tmp_path, ONE_HIT)
        exp = _expectations(tmp_path, ["Attach additional disks to VM ::2"])
        rc = sanitize_junit.main(
            ["--junit-dir", str(xml.parent), "--expectations", str(exp), "--strict"]
        )
        assert rc == 1
        err = capsys.readouterr().err
        assert "matched 1 testcases, declared 2" in err
        assert "Fix (matched fewer)" in err

    def test_non_strict_only_warns(self, tmp_path, capsys):
        xml = _write(tmp_path, ONE_HIT)
        exp = _expectations(tmp_path, ["Attach additional disks to VM ::2"])
        rc = sanitize_junit.main(
            ["--junit-dir", str(xml.parent), "--expectations", str(exp)]
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "WARNING: declared expectation matched 1 testcases, declared 2" in out

    def test_an_exact_count_is_accepted_and_both_firings_downgraded(
        self, tmp_path, capsys
    ):
        """What the exact count buys: declaring 2 is only clean when 2 fired, so
        a count wider than the guard can produce can no longer sit there
        absorbing a real firing of that same guard on another host."""
        xml = _write(tmp_path, SAME_NAME_TWICE)
        exp = _expectations(tmp_path, ["Attach additional disks to VM ::2"])
        rc = sanitize_junit.main(
            ["--junit-dir", str(xml.parent), "--expectations", str(exp), "--strict"]
        )
        assert rc == 0
        assert "WARNING" not in capsys.readouterr().out
        cases = list(ET.parse(xml).getroot().iter("testcase"))
        assert {case.get("name") for case in cases} == {
            "[host-a] Converge: role : Attach additional disks to VM",
            "[host-b] Converge: role : Attach additional disks to VM",
        }
        assert all(case.find("failure") is None for case in cases)

    def test_a_second_firing_of_one_guard_is_reported(self, tmp_path, capsys):
        """The same fixture declared at one: the extra firing stays a failure."""
        xml = _write(tmp_path, SAME_NAME_TWICE)
        exp = _expectations(tmp_path, ["Attach additional disks to VM"])
        rc = sanitize_junit.main(
            ["--junit-dir", str(xml.parent), "--expectations", str(exp), "--strict"]
        )
        assert rc == 1
        assert "matched 2 testcases, declared 1" in capsys.readouterr().err

    def test_an_unobserved_declaration_keeps_its_own_message(self, tmp_path, capsys):
        """Observed zero stays the never-observed arm, not a count mismatch."""
        xml = _write(tmp_path, ONE_HIT)
        exp = _expectations(tmp_path, ["A guard that no longer exists ::2"])
        rc = sanitize_junit.main(
            ["--junit-dir", str(xml.parent), "--expectations", str(exp), "--strict"]
        )
        assert rc == 1
        err = capsys.readouterr().err
        assert "never observed" in err
        assert "matched 0 testcases" not in err


class TestAbsentJunitDir:
    """Declarations that could not be observed are not a clean --strict run."""

    def test_strict_fails_when_the_junit_dir_is_absent(self, tmp_path, capsys):
        exp = _expectations(tmp_path, ["A declared guard"])
        rc = sanitize_junit.main(
            ["--junit-dir", str(tmp_path / "gone"), "--expectations", str(exp),
             "--strict"]
        )
        assert rc == 1
        assert "does not exist, so none could be observed" in capsys.readouterr().err

    def test_a_missing_junit_dir_is_tolerated_without_strict(self, tmp_path, capsys):
        exp = _expectations(tmp_path, ["A declared guard"])
        rc = sanitize_junit.main(
            ["--junit-dir", str(tmp_path / "gone"), "--expectations", str(exp)]
        )
        assert rc == 0
        assert "none could be observed" in capsys.readouterr().out
