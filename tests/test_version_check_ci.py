#!/usr/bin/env python3
"""Unit tests for scripts/version-check-ci.py."""

import io
import json
import unittest
from unittest.mock import MagicMock, patch

import pytest

from script_loader import load_script

version_check_ci = load_script("version-check-ci.py", register=True)


def _completed(stdout: str, stderr: str = "", returncode: int = 0):
    """Build a fake subprocess.CompletedProcess-shaped object."""
    proc = MagicMock()
    proc.stdout = stdout
    proc.stderr = stderr
    proc.returncode = returncode
    return proc


class _FakeOpen:
    """Captures open(..., 'w') writes by filename, keeping the filesystem clean."""

    def __init__(self):
        self.files: dict[str, io.StringIO] = {}
        self.makedirs = None

    def __call__(self, name, mode="r", *args, **kwargs):
        buf = io.StringIO()
        # Don't let StringIO.close() discard the buffer before we read it.
        buf.close = lambda: None  # type: ignore[method-assign]
        self.files[name] = buf
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=buf)
        cm.__exit__ = MagicMock(return_value=False)
        return cm

    def contents(self, name: str) -> str:
        return self.files[name].getvalue()


def _run_main(stdout, *, env, stderr="", returncode=0, argv=None):
    """Run version_check_ci.main() with subprocess/open/post mocked.

    Returns (exit_code, fake_open, posted_bodies).
    """
    fake_open = _FakeOpen()
    posted: list[str] = []

    with patch.object(version_check_ci.subprocess, "run",
                      return_value=_completed(stdout, stderr, returncode)), \
         patch("builtins.open", fake_open), \
         patch.object(version_check_ci.os, "makedirs") as fake_makedirs, \
         patch.object(version_check_ci, "upsert_mr_comment",
                      side_effect=lambda body, token_env=None: posted.append(body)), \
         patch.dict(version_check_ci.os.environ, env, clear=True):
        try:
            version_check_ci.main(argv or [])
            code = 0
        except SystemExit as e:
            code = e.code if e.code is not None else 0

    fake_open.makedirs = fake_makedirs
    return code, fake_open, posted


def _payload(services, summary_overrides=None):
    """Build a check-versions.py --json payload."""
    summary = {
        "total": len(services),
        "up_to_date": sum(
            1 for s in services if not s.get("update_available") and not s.get("error")
        ),
        "updates_available": sum(
            1 for s in services if s.get("update_available") and not s.get("held")
        ),
        "updates_held": sum(
            1 for s in services if s.get("update_available") and s.get("held")
        ),
        "errors": sum(1 for s in services if s.get("error")),
    }
    if summary_overrides:
        summary.update(summary_overrides)
    return json.dumps({"summary": summary, "services": services})


class TestExitCodes(unittest.TestCase):
    """Exit code reconciliation from the parsed --json summary."""

    def test_all_up_to_date_exits_zero(self):
        payload = _payload([
            {"name": "Foo", "current_version": "1.0", "latest_version": "1.0",
             "update_available": False},
        ])
        # check-versions.py would have returned 0 too; main() reconciles.
        code, _, _ = _run_main(payload, env={}, returncode=0)
        self.assertEqual(code, 0)

    def test_updates_available_exits_one(self):
        payload = _payload([
            {"name": "Foo", "current_version": "1.0", "latest_version": "1.1",
             "update_available": True},
        ])
        code, _, _ = _run_main(payload, env={}, returncode=1)
        self.assertEqual(code, 1)

    def test_errors_exit_two(self):
        payload = _payload([
            {"name": "Foo", "current_version": "1.0", "latest_version": None,
             "update_available": False, "error": "boom"},
        ])
        code, _, _ = _run_main(payload, env={}, returncode=2)
        self.assertEqual(code, 2)

    def test_errors_take_precedence_over_updates(self):
        """When both updates and errors are present, errors win → exit 2."""
        payload = _payload([
            {"name": "Up", "current_version": "1.0", "latest_version": "1.1",
             "update_available": True},
            {"name": "Bad", "current_version": "2.0", "latest_version": None,
             "update_available": False, "error": "boom"},
        ])
        code, _, _ = _run_main(payload, env={}, returncode=1)
        self.assertEqual(code, 2)


class TestParseFailureStub(unittest.TestCase):
    """A non-JSON check-versions.py stdout writes a self-describing stub
    artifact and exits 2 (not a silent empty/raw-text report)."""

    def test_unparseable_output_writes_stub_and_exits_two(self):
        code, fake_open, _ = _run_main(
            "this is not json", env={}, stderr="traceback here", returncode=0
        )
        self.assertEqual(code, 2)
        artifact = json.loads(fake_open.contents("version-report.json"))
        # Self-describing: carries the parse error plus the raw streams.
        self.assertIn("error", artifact)
        self.assertIn("not parseable", artifact["error"])
        self.assertEqual(artifact["stdout"], "this is not json")
        self.assertEqual(artifact["stderr"], "traceback here")

    def test_wrong_shape_json_writes_stub_and_exits_two(self):
        """Valid JSON that is not an object is a parse failure: exit 2 plus stub."""
        for shape in ("[]", "[1,2,3]", "null", "123", '"str"', "true"):
            with self.subTest(shape=shape):
                code, fake_open, _ = _run_main(shape, env={}, returncode=0)
                self.assertEqual(code, 2, f"{shape} should exit 2")
                artifact = json.loads(fake_open.contents("version-report.json"))
                self.assertIn("error", artifact)
                self.assertIn("not parseable", artifact["error"])
                self.assertEqual(artifact["stdout"], shape)

    def test_wrong_shape_json_posts_failure_section_in_mr(self):
        """In an MR pipeline, a non-dict payload posts the 'failed' section
        rather than an empty Updates/Errors table."""
        _, _, posted = _run_main(
            "[]", env={"CI_MERGE_REQUEST_IID": "42"}, stderr="boom", returncode=0
        )
        self.assertEqual(len(posted), 1)
        self.assertIn("### Version check failed", posted[0])

    def test_valid_output_persists_raw_json_artifact(self):
        payload = _payload([
            {"name": "Foo", "current_version": "1.0", "latest_version": "1.0",
             "update_available": False},
        ])
        _, fake_open, _ = _run_main(payload, env={}, returncode=0)
        # The artifact is the raw validated stdout, round-trips to the payload.
        self.assertEqual(
            json.loads(fake_open.contents("version-report.json")),
            json.loads(payload),
        )


class TestOutputFlag(unittest.TestCase):
    """--output redirects the report artifact; the default is unchanged."""

    PAYLOAD = _payload([
        {"name": "Foo", "current_version": "1.0", "latest_version": "1.0",
         "update_available": False},
    ])

    def test_default_is_version_report_json(self):
        """The CWD-relative default is the artifact path consumers' CI
        collects — it must not change when the flag is absent."""
        self.assertEqual(version_check_ci.DEFAULT_OUTPUT, "version-report.json")
        _, fake_open, _ = _run_main(self.PAYLOAD, env={}, returncode=0)
        self.assertEqual(list(fake_open.files), ["version-report.json"])

    def test_default_needs_no_directory(self):
        """A bare filename has no parent — don't call makedirs("")."""
        _, fake_open, _ = _run_main(self.PAYLOAD, env={}, returncode=0)
        fake_open.makedirs.assert_not_called()

    def test_output_flag_redirects_valid_report(self):
        _, fake_open, _ = _run_main(
            self.PAYLOAD, env={}, returncode=0, argv=["--output", "out/custom.json"]
        )
        self.assertEqual(list(fake_open.files), ["out/custom.json"])
        self.assertEqual(
            json.loads(fake_open.contents("out/custom.json")),
            json.loads(self.PAYLOAD),
        )
        # The artifacts subdir is created, so the first run doesn't
        # FileNotFoundError on a path CI has not pre-made.
        fake_open.makedirs.assert_called_once_with("out", exist_ok=True)

    def test_output_flag_redirects_parse_failure_stub(self):
        """The stub written on the parse-failure branch honours --output too
        (both writes are threaded, not just the happy path)."""
        code, fake_open, _ = _run_main(
            "not json", env={}, stderr="boom", returncode=0,
            argv=["--output", "custom.json"],
        )
        self.assertEqual(code, 2)
        self.assertEqual(list(fake_open.files), ["custom.json"])
        artifact = json.loads(fake_open.contents("custom.json"))
        self.assertIn("not parseable", artifact["error"])

    def test_unknown_flag_is_rejected(self):
        """argparse rejects an unknown flag instead of silently ignoring it."""
        with patch.dict(version_check_ci.os.environ, {}, clear=True):
            with self.assertRaises(SystemExit) as cm:
                version_check_ci.main(["--bogus"])
        self.assertEqual(cm.exception.code, 2)


class TestMrComment(unittest.TestCase):
    """MR comment composition (only inside an MR pipeline)."""

    MR_ENV = {"CI_MERGE_REQUEST_IID": "42"}

    def test_no_comment_outside_mr_pipeline(self):
        payload = _payload([
            {"name": "Foo", "current_version": "1.0", "latest_version": "1.1",
             "update_available": True},
        ])
        _, _, posted = _run_main(payload, env={}, returncode=1)
        self.assertEqual(posted, [], "must not post a comment outside an MR")

    def test_comment_has_both_updates_and_errors_sections(self):
        payload = _payload([
            {"name": "Up", "current_version": "1.0", "latest_version": "1.1",
             "update_available": True, "notes": "minor bump"},
            {"name": "Bad", "current_version": "2.0", "latest_version": None,
             "update_available": False, "error": "connection refused"},
        ])
        _, _, posted = _run_main(payload, env=self.MR_ENV, returncode=1)
        self.assertEqual(len(posted), 1)
        body = posted[0]
        self.assertIn("### Updates available", body)
        self.assertIn("### Errors", body)
        # The update row and the error row both appear.
        self.assertIn("Up", body)
        self.assertIn("Bad", body)
        self.assertIn("connection refused", body)

    def test_held_updates_excluded_from_update_table(self):
        """A held service with update_available=True must not appear in the
        update table (the `not svc.get('held')` guard)."""
        payload = _payload([
            {"name": "MetalLB", "current_version": "0.15.3",
             "latest_version": "0.16.0", "update_available": True,
             "held": True, "notes": "intentionally held back"},
            {"name": "Foo", "current_version": "1.0", "latest_version": "1.1",
             "update_available": True},
        ])
        # summary.updates_available counts only the non-held Foo → exit 1.
        code, _, posted = _run_main(payload, env=self.MR_ENV, returncode=1)
        self.assertEqual(code, 1)
        self.assertEqual(len(posted), 1)
        body = posted[0]
        self.assertIn("### Updates available", body)
        self.assertIn("Foo", body)
        # MetalLB is held → excluded from the actionable update table.
        self.assertNotIn("MetalLB", body)

    def test_all_held_no_actionable_updates_no_comment(self):
        """If the only update is held, there are no actionable updates and no
        errors → exit 0 and no MR comment."""
        payload = _payload([
            {"name": "MetalLB", "current_version": "0.15.3",
             "latest_version": "0.16.0", "update_available": True,
             "held": True, "notes": "held"},
        ])
        code, _, posted = _run_main(payload, env=self.MR_ENV, returncode=0)
        self.assertEqual(code, 0)
        self.assertEqual(posted, [], "held-only run posts nothing")

    def test_no_empty_update_table_when_summary_diverges(self):
        """Defensive: a forged payload whose summary.updates_available > 0 but
        whose only update is held must NOT emit an Updates header with zero
        rows (the section is gated on the built row list, not the counter)."""
        payload = _payload(
            [
                {"name": "MetalLB", "current_version": "0.15.3",
                 "latest_version": "0.16.0", "update_available": True,
                 "held": True, "notes": "held"},
            ],
            # Hand-forge a divergent counter the real producer could never emit.
            summary_overrides={"updates_available": 1},
        )
        _, _, posted = _run_main(payload, env=self.MR_ENV, returncode=1)
        # No actionable (non-held) update and no error -> nothing to post.
        self.assertEqual(posted, [], "must not post an empty Updates table")

    def test_parse_failure_posts_failure_section_in_mr(self):
        """In an MR pipeline, an unparseable output posts a 'failed' section
        (no structured services to itemize)."""
        _, _, posted = _run_main(
            "garbage", env=self.MR_ENV, stderr="boom", returncode=0
        )
        self.assertEqual(len(posted), 1)
        self.assertIn("### Version check failed", posted[0])
        self.assertIn("boom", posted[0])


class TestSubprocessFailureModes(unittest.TestCase):
    """Timeout / exec failure of the underlying check-versions.py call."""

    def test_timeout_exits_two(self):
        import subprocess
        with patch.object(version_check_ci.subprocess, "run",
                          side_effect=subprocess.TimeoutExpired(cmd="x", timeout=300)), \
             patch.dict(version_check_ci.os.environ, {}, clear=True):
            with self.assertRaises(SystemExit) as cm:
                version_check_ci.main([])
        self.assertEqual(cm.exception.code, 2)

    def test_oserror_exits_two(self):
        with patch.object(version_check_ci.subprocess, "run",
                          side_effect=OSError("no such file")), \
             patch.dict(version_check_ci.os.environ, {}, clear=True):
            with self.assertRaises(SystemExit) as cm:
                version_check_ci.main([])
        self.assertEqual(cm.exception.code, 2)


MR_CREDS = {
    "CI_API_V4_URL": "https://gitlab.example.com/api/v4",
    "CI_PROJECT_ID": "1",
    "CI_MERGE_REQUEST_IID": "42",
    "GITLAB_API_TOKEN": "tok",
}


def _response(body: bytes):
    resp = MagicMock()
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    resp.read.return_value = body
    return resp


BOT = _response(b'{"id": 5}')


def _note(note_id, author_id, body):
    return {"id": note_id, "system": False, "author": {"id": author_id}, "body": body}


class TestUpsertMrComment(unittest.TestCase):
    """The note is refreshed, not re-posted, on every pipeline of one MR."""

    def setUp(self):
        version_check_ci._BOT_USER_ID = None

    def test_skips_when_credentials_incomplete(self):
        with patch.object(version_check_ci, "urlopen") as mock_urlopen, \
             patch.dict(version_check_ci.os.environ, {}, clear=True):
            version_check_ci.upsert_mr_comment("hi")
        mock_urlopen.assert_not_called()

    def test_honours_a_custom_token_env(self):
        env = {k: v for k, v in MR_CREDS.items() if k != "GITLAB_API_TOKEN"}
        env["BOT_TOKEN"] = "other"
        with patch.object(version_check_ci, "urlopen",
                          side_effect=[BOT, _response(b"[]"), _response(b"")]) as mock_urlopen, \
             patch.dict(version_check_ci.os.environ, env, clear=True):
            version_check_ci.upsert_mr_comment("hello", "BOT_TOKEN")
        self.assertEqual(mock_urlopen.call_count, 3)
        self.assertEqual(
            mock_urlopen.call_args_list[2][0][0].headers.get("Private-token"), "other"
        )

    def test_posts_when_no_marked_note_exists(self):
        with patch.object(version_check_ci, "urlopen",
                          side_effect=[BOT, _response(b"[]"), _response(b"")]) as mock_urlopen, \
             patch.dict(version_check_ci.os.environ, MR_CREDS, clear=True):
            version_check_ci.upsert_mr_comment("hello")
        req = mock_urlopen.call_args_list[2][0][0]
        self.assertEqual(req.get_method(), "POST")
        self.assertIn("/merge_requests/42/notes", req.full_url)
        self.assertEqual(req.headers.get("Private-token"), "tok")

    def test_updates_the_note_this_job_left_last_time(self):
        notes = json.dumps([
            {"id": 7, "system": True, "body": "changed the description"},
            _note(9, 5, version_check_ci.MARKER + "\n## Version Check"),
        ]).encode()
        with patch.object(version_check_ci, "urlopen",
                          side_effect=[BOT, _response(notes), _response(b"")]) as mock_urlopen, \
             patch.dict(version_check_ci.os.environ, MR_CREDS, clear=True):
            version_check_ci.upsert_mr_comment(version_check_ci.MARKER + "\nnew body")
        req = mock_urlopen.call_args_list[2][0][0]
        self.assertEqual(req.get_method(), "PUT")
        self.assertTrue(req.full_url.endswith("/notes/9"))

    def test_a_humans_note_quoting_the_marker_is_never_edited(self):
        """The marker alone is not proof of authorship; the bot posts instead."""
        notes = json.dumps([
            _note(9, 11, version_check_ci.MARKER + "\nlooks wrong to me"),
        ]).encode()
        with patch.object(version_check_ci, "urlopen",
                          side_effect=[BOT, _response(notes), _response(b"")]) as mock_urlopen, \
             patch.dict(version_check_ci.os.environ, MR_CREDS, clear=True):
            version_check_ci.upsert_mr_comment(version_check_ci.MARKER + "\nnew body")
        self.assertEqual(mock_urlopen.call_args_list[2][0][0].get_method(), "POST")

    def test_the_bots_identity_is_fetched_once_per_run(self):
        notes = json.dumps([_note(9, 5, version_check_ci.MARKER)]).encode()
        with patch.object(
            version_check_ci, "urlopen",
            side_effect=[BOT, _response(notes), _response(b""),
                         _response(notes), _response(b"")],
        ) as mock_urlopen, \
             patch.dict(version_check_ci.os.environ, MR_CREDS, clear=True):
            version_check_ci.upsert_mr_comment(version_check_ci.MARKER + "\na")
            version_check_ci.upsert_mr_comment(version_check_ci.MARKER + "\nb")
        user_calls = [
            c for c in mock_urlopen.call_args_list if c[0][0].full_url.endswith("/user")
        ]
        self.assertEqual(len(user_calls), 1)

    def test_an_unidentifiable_token_degrades_to_a_post(self):
        """Without an identity the bot must not edit anything."""
        with patch.object(version_check_ci, "urlopen",
                          side_effect=[OSError("api down"), _response(b"")]) as mock_urlopen, \
             patch.dict(version_check_ci.os.environ, MR_CREDS, clear=True):
            version_check_ci.upsert_mr_comment("hello")
        self.assertEqual(mock_urlopen.call_args_list[1][0][0].get_method(), "POST")

    def test_a_failed_note_listing_degrades_to_a_post(self):
        with patch.object(version_check_ci, "urlopen",
                          side_effect=[BOT, OSError("api down"), _response(b"")]) as mock_urlopen, \
             patch.dict(version_check_ci.os.environ, MR_CREDS, clear=True):
            version_check_ci.upsert_mr_comment("hello")
        self.assertEqual(mock_urlopen.call_args_list[2][0][0].get_method(), "POST")


class TestSummaryCounterGuard(unittest.TestCase):
    """A non-numeric counter from a skewed producer must not crash the wrapper."""

    def test_string_counters_are_treated_as_zero(self):
        payload = _payload(
            [{"name": "Foo", "current_version": "1.0", "latest_version": "1.0",
              "update_available": False}],
            summary_overrides={"errors": "0", "updates_available": "2"},
        )
        code, _, posted = _run_main(payload, env={}, returncode=0)
        self.assertEqual(code, 0)
        self.assertEqual(posted, [])

    def test_bool_is_not_a_counter(self):
        self.assertEqual(version_check_ci._count(True), 0)
        self.assertEqual(version_check_ci._count(3), 3)
        self.assertEqual(version_check_ci._count(None), 0)


class TestCheckerExitCodeContract(unittest.TestCase):
    """A CHECK_VERSIONS_CMD exit outside 0/1/2 is reported, not swallowed."""

    def test_out_of_contract_returncode_exits_two(self):
        payload = _payload([
            {"name": "Foo", "current_version": "1.0", "latest_version": "1.0",
             "update_available": False},
        ])
        code, _, _ = _run_main(payload, env={}, returncode=3)
        self.assertEqual(code, 2)


class TestMrCommentIsInertMarkdown(unittest.TestCase):
    """Upstream error text reaches a note posted as the token owner."""

    def test_a_quick_action_in_an_error_body_is_neutralised(self):
        payload = _payload([
            {"name": "Bad", "current_version": "2.0", "latest_version": None,
             "update_available": False, "error": "boom\n/merge"},
        ])
        _, _, posted = _run_main(payload, env={"CI_MERGE_REQUEST_IID": "42"}, returncode=2)
        self.assertEqual(len(posted), 1)
        self.assertNotIn("\n/merge", posted[0])
        self.assertIn(" /merge", posted[0])

    def test_a_backtick_run_in_the_failure_output_cannot_close_the_fence(self):
        _, _, posted = _run_main(
            "garbage", env={"CI_MERGE_REQUEST_IID": "42"},
            stderr="```\n/merge", returncode=0,
        )
        body = posted[0]
        fence = "`" * 4
        self.assertEqual(body.count(fence), 2)
        self.assertIn(" /merge", body)


class TestServicesGuard(unittest.TestCase):
    """_services() normalizes the external `services` payload so a malformed
    shape (null, non-list, or non-dict entries) can't crash the iterations that
    build the log output and MR comment, bypassing the stub-artifact contract."""

    def test_absent_or_null_services_returns_empty(self):
        self.assertEqual(version_check_ci._services({}), [])
        self.assertEqual(version_check_ci._services({"services": None}), [])

    def test_non_list_services_returns_empty(self):
        self.assertEqual(version_check_ci._services({"services": {"a": 1}}), [])

    def test_filters_non_dict_entries(self):
        data = {"services": [{"name": "a"}, "x", 3, None, {"name": "b"}]}
        self.assertEqual(
            version_check_ci._services(data), [{"name": "a"}, {"name": "b"}]
        )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
