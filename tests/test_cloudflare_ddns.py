"""Unit tests for kubernetes/ddns/cloudflare-ddns.py, the DDNS CronJob program."""
import io
import json
import os
import re
import urllib.error
from pathlib import Path

import pytest
from script_loader import load_path

REPO = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO / "kubernetes" / "ddns" / "cloudflare-ddns.py"
# The only hosts the program may name: the API and the IPv4 detection providers.
ALLOWED_HOSTS = {
    "api.cloudflare.com",
    "api.ipify.org",
    "ipv4.icanhazip.com",
    "checkip.amazonaws.com",
}

# The program is configured only by env, so the suite supplies the env the
# CronJob supplies. Any zone with a dot in it exercises the same code paths.
TEST_ZONE = "example.test"
TEST_RECORDS = "@, direct:false, git:false, vpn:false"


def _load(zone=TEST_ZONE, records=TEST_RECORDS):
    previous = {key: os.environ.get(key) for key in ("DDNS_ZONE", "DDNS_RECORDS")}
    os.environ["DDNS_ZONE"] = zone
    os.environ["DDNS_RECORDS"] = records
    try:
        return load_path(MODULE_PATH)
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture(scope="module")
def ddns():
    return _load()


def test_the_program_carries_no_site_data(ddns):
    """Zone and record list come from env only. A built-in list would keep
    working after the CronJob stopped passing one, hiding the breakage."""
    source = MODULE_PATH.read_text()
    assert "DEFAULT_RECORDS" not in source
    assert "cluster_external_domain" not in source
    hosts = set(re.findall(r"\b(?:[a-z0-9-]+\.)+(?:com|net|org|io|dev|test)\b", source))
    assert hosts <= ALLOWED_HOSTS, hosts - ALLOWED_HOSTS
    assert ddns.ZONE == TEST_ZONE
    assert ddns.RECORD_ERRORS == ()
    assert ddns.RECORDS == (
        (TEST_ZONE, True),
        (f"direct.{TEST_ZONE}", False),
        (f"git.{TEST_ZONE}", False),
        (f"vpn.{TEST_ZONE}", False),
    )


def test_parse_records_reads_the_cronjob_spec(ddns):
    """The CronJob supplies the record list, so the parser is the contract."""
    zone = ddns.ZONE
    # A bare label is proxied by default; ':false' turns it off.
    assert ddns.parse_records("www") == (((f"www.{zone}", True),), ())
    assert ddns.parse_records("www:false") == (((f"www.{zone}", False),), ())
    # Whitespace and empty entries between commas are ignored.
    assert ddns.parse_records(" www , ,") == (((f"www.{zone}", True),), ())


@pytest.mark.parametrize("spec, reason", [
    ("a:bogus", "proxied flag other than true or false"),
    ("b:false:oops", "more than one ':'"),
])
def test_parse_records_reports_a_malformed_entry(ddns, spec, reason):
    """Dropping one silently would stop updating a WAN name."""
    parsed, errors = ddns.parse_records(spec)
    assert parsed == ()
    assert len(errors) == 1 and spec in errors[0] and reason in errors[0], errors


def test_parse_records_rejects_an_empty_or_dot_edged_label(ddns):
    """A label like '' or '.' would build `.zone` and POST it as a new record."""
    for spec in (":false", " :false", ".:false", "www.:false", "a..b:false"):
        parsed, errors = ddns.parse_records(spec)
        assert parsed == () and len(errors) == 1, (parsed, errors)
        assert "empty or dot-edged label" in errors[0]
    # A mixed spec still reports the bad entry beside the good one.
    parsed, errors = ddns.parse_records(" :false, www")
    assert parsed == ((f"www.{ddns.ZONE}", True),) and len(errors) == 1


def test_get_public_ip_skips_non_global_and_non_ipv4(ddns, monkeypatch):
    replies = {
        "a": "10.1.2.3", "b": "not-an-ip", "c": "203.0.113.9", "d": "8.8.4.4", "e": "8.8.4.4",
    }
    monkeypatch.setattr(ddns, "_fetch_ip", lambda url, timeout=10: replies[url])
    assert ddns.get_public_ip(services=("a", "b", "c", "d", "e"), attempts=1) == "8.8.4.4"


@pytest.mark.parametrize("reply", ["10.1.2.3", "100.64.0.1", "not-an-ip", "::1", ""])
def test_two_providers_agreeing_on_an_unusable_reply_reach_no_quorum(ddns, monkeypatch, reply):
    """Agreement does not make a private or non-IPv4 reply publishable."""
    monkeypatch.setattr(ddns, "_fetch_ip", lambda url, timeout=10: reply)
    assert ddns.get_public_ip(services=("a", "b"), attempts=1) is None


def test_get_public_ip_needs_two_providers_to_agree(ddns, monkeypatch, capsys):
    """One wrong or hijacked answer must not repoint every record."""
    replies = {"a": "8.8.4.4", "b": "1.1.1.1", "c": "9.9.9.9"}
    monkeypatch.setattr(ddns, "_fetch_ip", lambda url, timeout=10: replies[url])
    assert ddns.get_public_ip(services=("a", "b", "c"), attempts=1) is None
    assert "disagree" in capsys.readouterr().err
    replies["c"] = "1.1.1.1"
    assert ddns.get_public_ip(services=("a", "b", "c"), attempts=1) == "1.1.1.1"


def test_get_public_ip_stops_at_the_quorum(ddns, monkeypatch):
    """Each extra provider is a round-trip on a five-minutely job."""
    asked = []
    monkeypatch.setattr(
        ddns, "_fetch_ip", lambda url, timeout=10: asked.append(url) or "8.8.4.4"
    )
    assert ddns.get_public_ip(services=("a", "b", "c"), attempts=1) == "8.8.4.4"
    assert asked == ["a", "b"]


def test_a_single_provider_list_is_its_own_quorum(ddns, monkeypatch):
    monkeypatch.setattr(ddns, "_fetch_ip", lambda url, timeout=10: "8.8.4.4")
    assert ddns.get_public_ip(services=("a",), attempts=1) == "8.8.4.4"


def test_the_shipped_providers_can_reach_the_quorum(ddns):
    assert len(set(ddns.IP_SERVICES)) >= ddns.IP_QUORUM


def test_get_public_ip_retries_then_gives_up(ddns, monkeypatch):
    calls = []

    def boom(url, timeout=10):
        calls.append(url)
        raise OSError("no route to host")

    monkeypatch.setattr(ddns, "_fetch_ip", boom)
    slept = []
    assert ddns.get_public_ip(services=("a",), attempts=3, sleep=slept.append) is None
    assert len(calls) == 3
    assert slept == [ddns.IP_RETRY_SLEEP, ddns.IP_RETRY_SLEEP]


def test_build_record_body_preserves_every_terraform_owned_decoration(ddns):
    """The PUT replaces the WHOLE record, so every Terraform-owned decoration must
    be carried forward; one this script omits is erased on the next address
    change and reads afterwards as Terraform drift."""
    existing = {
        "ttl": 60,
        "proxied": True,
        "comment": "Terraform-owned",
        "tags": ["managed"],
        "settings": {"ipv4_only": True},
    }
    body = ddns.build_record_body("git.example.test", "198.51.100.7", False, existing)
    assert body == {
        "type": "A",
        "name": "git.example.test",
        "content": "198.51.100.7",
        "ttl": 60,
        "proxied": True,
        "comment": "Terraform-owned",
        "tags": ["managed"],
        "settings": {"ipv4_only": True},
    }


def test_build_record_body_drops_null_decorations_but_keeps_empty_ones(ddns):
    """Cloudflare returns an unset decoration as null — sending it back would be
    a type error — while a deliberately-empty one is a value the record has."""
    body = ddns.build_record_body(
        "vpn.example.test",
        "198.51.100.7",
        False,
        {"ttl": 1, "proxied": False, "comment": None, "tags": [], "settings": {}},
    )
    assert "comment" not in body
    assert body["tags"] == []
    assert body["settings"] == {}


def test_build_record_body_on_create_uses_the_literal_proxied(ddns):
    body = ddns.build_record_body("example.test", "198.51.100.7", True, None)
    assert body["proxied"] is True
    assert body["ttl"] == 1
    assert not any(key in body for key in ddns.PRESERVED_FIELDS)


def test_update_record_is_a_noop_when_the_ip_is_unchanged(ddns, monkeypatch):
    seen = []

    def fake_api(token, url, method="GET", data=None, timeout=30):
        seen.append((method, url))
        return {"success": True, "result": [{"id": "r1", "content": "198.51.100.7"}]}

    monkeypatch.setattr(ddns, "api_request", fake_api)
    assert ddns.update_record("tok", "z1", "git.example.test", "198.51.100.7", False) is True
    assert [m for m, _ in seen] == ["GET"]


def test_update_record_posts_when_the_record_is_absent(ddns, monkeypatch):
    seen = []

    def fake_api(token, url, method="GET", data=None, timeout=30):
        seen.append((method, data))
        if method == "GET":
            return {"success": True, "result": []}
        return {"success": True}

    monkeypatch.setattr(ddns, "api_request", fake_api)
    assert ddns.update_record("tok", "z1", "vpn.example.test", "198.51.100.7", False) is True
    assert seen[-1][0] == "POST"


def test_update_record_puts_the_preserved_decorations_end_to_end(ddns, monkeypatch):
    """build_record_body is only half the guarantee — this is the body that
    actually reaches the wire on the PUT path."""
    seen = []

    def fake_api(token, url, method="GET", data=None, timeout=30):
        seen.append((method, url, data))
        if method == "GET":
            return {
                "success": True,
                "result": [
                    {
                        "id": "r1",
                        "content": "198.51.100.1",
                        "ttl": 60,
                        "proxied": True,
                        "comment": "Terraform-owned",
                        "tags": ["managed"],
                        "settings": {"ipv4_only": True},
                    }
                ],
            }
        return {"success": True}

    monkeypatch.setattr(ddns, "api_request", fake_api)
    assert ddns.update_record("tok", "z1", "git.example.test", "198.51.100.7", False) is True
    method, url, data = seen[-1]
    assert method == "PUT"
    assert url.endswith("/dns_records/r1")
    assert data["content"] == "198.51.100.7"
    assert data["comment"] == "Terraform-owned"
    assert data["tags"] == ["managed"]
    assert data["settings"] == {"ipv4_only": True}


def test_update_record_refuses_an_ambiguous_multi_record_name(ddns, monkeypatch, capsys):
    """Two A records for one name is ambiguous ownership: updating just the
    first leaves the stale sibling answering intermittently — a round-robin half
    outage — and picking one at all guesses which record Terraform owns."""
    seen = []

    def fake_api(token, url, method="GET", data=None, timeout=30):
        seen.append((method, url))
        return {
            "success": True,
            "result": [
                {"id": "r1", "content": "198.51.100.1"},
                {"id": "r2", "content": "198.51.100.2"},
            ],
        }

    monkeypatch.setattr(ddns, "api_request", fake_api)
    assert ddns.update_record("tok", "z1", "vpn.example.test", "203.0.113.9", False) is False
    # Only the query happened: nothing was written, not even the first record.
    assert [m for m, _ in seen] == ["GET"]
    captured = capsys.readouterr()
    assert "ambiguous ownership" in captured.err
    # stdout is the per-record report a healthy run also writes; a refusal buried
    # there reads as success.
    assert "ambiguous ownership" not in captured.out


def test_records_are_valid_rejects_a_non_boolean_proxied_flag(ddns, capsys):
    assert ddns.records_are_valid((("git", "yes"),)) is False
    assert "non-boolean proxied flag" in capsys.readouterr().err
    # Terraform-style list entries stay accepted — the shape check must not
    # tighten pair to tuple-only.
    assert ddns.records_are_valid((["git", True],)) is True


# An empty list exits 0 having managed nothing; a blank name or an
# unsubstituted zone sends an unfiltered `name=` query that matches any row.
@pytest.mark.parametrize("zone, records, message", [
    (TEST_ZONE, (), "RECORDS is empty"),
    (TEST_ZONE, (("", False),), "no record name"),
    (TEST_ZONE, (("   ", True),), "no record name"),
    (TEST_ZONE, ((None, False),), "no record name"),
    (TEST_ZONE, (("git.example.test", False), ("", True)), "no record name"),
    ("", None, "DDNS_ZONE is empty or malformed"),
    ("nodots", None, "DDNS_ZONE is empty or malformed"),
    ("${cluster_external_domain}", None, "DDNS_ZONE is empty or malformed"),
    # A dotted placeholder passes the dot check, so only the marker test stops it.
    ("ddns.${cluster_external_domain}", None, "DDNS_ZONE is empty or malformed"),
])
def test_main_fails_closed_before_any_call(ddns, monkeypatch, capsys, zone, records, message):
    monkeypatch.setenv("CF_API_TOKEN", "tok")
    monkeypatch.setattr(ddns, "ZONE", zone)
    if records is not None:
        monkeypatch.setattr(ddns, "RECORDS", records)
    calls = []
    monkeypatch.setattr(ddns, "get_public_ip", lambda *a, **k: calls.append("ip") or "203.0.113.9")
    monkeypatch.setattr(ddns, "api_request", lambda *a, **k: calls.append("api"))
    assert ddns.main() == 1
    assert calls == []
    assert message in capsys.readouterr().err


def test_a_malformed_spec_reaches_main_from_the_environment(monkeypatch, capsys):
    bad = _load(records="www, a:bogus")
    monkeypatch.setenv("CF_API_TOKEN", "tok")
    calls = []
    monkeypatch.setattr(bad, "get_public_ip", lambda *a, **k: calls.append("ip"))
    monkeypatch.setattr(bad, "api_request", lambda *a, **k: calls.append("api"))
    assert bad.main() == 1
    assert calls == []
    assert "DDNS_RECORDS entry 'a:bogus'" in capsys.readouterr().err


def _main_with_record_results(ddns, monkeypatch, results):
    """Drive main() to the record loop, with update_record returning `results`."""
    monkeypatch.setenv("CF_API_TOKEN", "tok")
    records = tuple((f"r{i}.example.test", False) for i in range(len(results)))
    monkeypatch.setattr(ddns, "RECORDS", records)
    monkeypatch.setattr(ddns, "get_public_ip", lambda *a, **k: "203.0.113.9")
    monkeypatch.setattr(ddns, "get_zone_id", lambda token: "zone-id")
    replies = iter(results)
    attempted = []
    monkeypatch.setattr(
        ddns, "update_record", lambda token, zone, name, ip, proxied: attempted.append(name) or next(replies)
    )
    return attempted


def test_main_reports_complete_only_when_every_record_succeeds(ddns, monkeypatch, capsys):
    _main_with_record_results(ddns, monkeypatch, [True, True])
    assert ddns.main() == 0
    out = capsys.readouterr()
    assert "DDNS update complete" in out.out
    assert "FAILED" not in out.err


def test_main_counts_failed_records_and_never_prints_complete(ddns, monkeypatch, capsys):
    attempted = _main_with_record_results(ddns, monkeypatch, [False, True, False])
    assert ddns.main() == 1
    # Every record is attempted even after the first failure.
    assert len(attempted) == 3
    out = capsys.readouterr()
    assert "DDNS update complete" not in out.out
    assert "DDNS update FAILED for 2 record(s)" in out.err


def test_get_zone_id_rejects_a_zone_the_api_did_not_actually_match(ddns, monkeypatch):
    """The unfiltered-list shape: rows come back, none of them ours."""
    monkeypatch.setattr(
        ddns,
        "api_request",
        lambda *a, **k: {"success": True, "result": [{"id": "other", "name": "not-ours.com"}]},
    )
    assert ddns.get_zone_id("tok", "example.test") is None


def test_get_zone_id_returns_the_matching_zone(ddns, monkeypatch):
    monkeypatch.setattr(
        ddns,
        "api_request",
        lambda *a, **k: {
            "success": True,
            "result": [{"id": "other", "name": "not-ours.com"}, {"id": "z1", "name": "example.test"}],
        },
    )
    assert ddns.get_zone_id("tok", "example.test") == "z1"


CF_ERRORS = {
    "success": False,
    "errors": [{"code": 9109, "message": "Invalid access token"}, "rate limited"],
    "messages": [],
}


def test_get_zone_id_logs_cloudflares_errors_on_success_false(ddns, monkeypatch, capsys):
    """A 2xx success:false body names its cause only in the errors array."""
    monkeypatch.setattr(ddns, "api_request", lambda *a, **k: CF_ERRORS)
    assert ddns.get_zone_id("tok", TEST_ZONE) is None
    err = capsys.readouterr().err
    assert "failed to get the zone id" in err
    assert "9109: Invalid access token" in err and "rate limited" in err


@pytest.mark.parametrize("zone", [{"name": TEST_ZONE}, {"name": TEST_ZONE, "id": ""}],
                         ids=["absent", "empty"])
def test_get_zone_id_names_a_matched_zone_without_an_id(ddns, monkeypatch, capsys, zone):
    monkeypatch.setattr(ddns, "api_request", lambda *a, **k: {"success": True, "result": [zone]})
    assert ddns.get_zone_id("tok", TEST_ZONE) is None
    assert f"zone {TEST_ZONE!r} has no id in the API response" in capsys.readouterr().err


@pytest.mark.parametrize("reply", [None, CF_ERRORS], ids=["no-reply", "success-false"])
def test_update_record_refuses_to_write_when_the_query_failed(ddns, monkeypatch, capsys, reply):
    """A failed query reads like "no such record", and a POST would duplicate it."""
    seen = []
    monkeypatch.setattr(ddns, "api_request", lambda t, u, method="GET", **k: seen.append(method) or reply)
    assert ddns.update_record("tok", "z1", f"git.{TEST_ZONE}", "198.51.100.7", False) is False
    assert seen == ["GET"]
    err = capsys.readouterr().err
    assert "failed to query the existing record" in err
    assert ("9109: Invalid access token" in err) is (reply is not None)


def test_update_record_logs_cloudflares_errors_on_a_failed_write(ddns, monkeypatch, capsys):
    def fake_api(token, url, method="GET", data=None, timeout=30):
        if method == "GET":
            return {"success": True, "result": [{"id": "r1", "content": "198.51.100.1"}]}
        return CF_ERRORS

    monkeypatch.setattr(ddns, "api_request", fake_api)
    assert ddns.update_record("tok", "z1", f"git.{TEST_ZONE}", "198.51.100.7", False) is False
    captured = capsys.readouterr()
    assert "update FAILED" in captured.err and "9109: Invalid access token" in captured.err
    assert "FAILED" not in captured.out


@pytest.mark.parametrize("data", [None, {"success": False}, {"success": False, "errors": []},
                                  {"success": False, "errors": "x"}])
def test_api_errors_is_empty_when_cloudflare_names_none(ddns, data):
    assert ddns._api_errors(data) == ""


# A name carrying `&` or `+` must not break out of its query parameter.
@pytest.mark.parametrize("call, row, query", [
    (lambda d: d.update_record("tok", "z1", "a+b&x=1.test", "198.51.100.7", False),
     {"id": "r1", "content": "198.51.100.7"}, "name=a%2Bb%26x%3D1.test"),
    (lambda d: d.get_zone_id("tok", "a+b&x=1.test"),
     {"id": "z1", "name": "a+b&x=1.test"}, "/zones?name=a%2Bb%26x%3D1.test"),
], ids=["record-name", "zone"])
def test_the_query_name_is_percent_encoded(ddns, monkeypatch, call, row, query):
    seen = []
    monkeypatch.setattr(
        ddns, "api_request",
        lambda token, url, **k: seen.append(url) or {"success": True, "result": [row]},
    )
    assert call(ddns)
    assert seen[0].endswith(query)


def test_parse_records_rejects_a_full_name_where_a_label_belongs(ddns):
    """`git.example.test` under zone example.test would build `git.example.test.example.test`."""
    for spec in (TEST_ZONE, f"git.{TEST_ZONE}", f"git.{TEST_ZONE}:false"):
        parsed, errors = ddns.parse_records(spec, zone=TEST_ZONE)
        assert parsed == (), parsed
        assert len(errors) == 1 and "full name where a label belongs" in errors[0], errors
    assert ddns.parse_records("git.lab", zone=TEST_ZONE) == (((f"git.lab.{TEST_ZONE}", True),), ())


@pytest.mark.parametrize("spec", ["@", "@:true", "www", "www:false", "a.b:TRUE", " @ , www "])
def test_records_accept_the_documented_spellings(ddns, spec):
    parsed, errors = ddns.parse_records(spec, zone=TEST_ZONE)
    assert errors == ()
    assert ddns.records_are_valid(parsed) is True


def test_every_failure_line_goes_to_stderr(ddns, monkeypatch, capsys):
    """The CronJob's stdout is the per-record report; an error there reads as success."""
    monkeypatch.delenv("CF_API_TOKEN", raising=False)
    assert ddns.main() == 1
    monkeypatch.setattr(ddns, "api_request", lambda *a, **k: {"success": False})
    assert ddns.get_zone_id("tok", TEST_ZONE) is None
    captured = capsys.readouterr()
    assert "ERROR" not in captured.out
    assert "CF_API_TOKEN not set" in captured.err and "zone id" in captured.err


@pytest.mark.parametrize(("ip", "zone_id"), [(None, "z1"), ("203.0.113.9", None)])
def test_main_fails_when_the_ip_or_zone_lookup_fails(ddns, monkeypatch, ip, zone_id):
    """A green run after a failed lookup would leave DNS stale unnoticed."""
    attempted = _main_with_record_results(ddns, monkeypatch, [True])
    monkeypatch.setattr(ddns, "get_public_ip", lambda *a, **k: ip)
    monkeypatch.setattr(ddns, "get_zone_id", lambda token: zone_id)
    assert ddns.main() == 1
    assert attempted == []


class _Reply:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def test_api_request_sends_the_json_body_and_the_bearer_token(ddns, monkeypatch):
    sent = []

    def urlopen(req, timeout):
        sent.append((req, timeout))
        return _Reply(b'{"success": true}')

    monkeypatch.setattr(ddns.urllib.request, "urlopen", urlopen)
    url = "https://api.cloudflare.com/client/v4/zones/z/dns_records/r"
    payload = {"type": "A", "content": "203.0.113.9"}
    assert ddns.api_request("tok", url, method="PUT", data=payload) == {"success": True}
    req, timeout = sent[0]
    assert req.get_method() == "PUT"
    assert req.full_url == url
    assert json.loads(req.data) == payload
    assert req.get_header("Authorization") == "Bearer tok"
    assert req.get_header("Content-type") == "application/json"
    assert timeout == 30


def test_api_request_logs_an_http_error_body_and_returns_none(ddns, monkeypatch, capsys):
    def urlopen(req, timeout):
        raise urllib.error.HTTPError(
            req.full_url, 403, "Forbidden", {},
            io.BytesIO(b'{"errors": [{"code": 9109, "message": "Invalid access token"}]}'),
        )

    monkeypatch.setattr(ddns.urllib.request, "urlopen", urlopen)
    url = "https://api.cloudflare.com/client/v4/zones"
    assert ddns.api_request("tok", url) is None
    err = capsys.readouterr().err
    assert "HTTP 403 Forbidden on GET " + url in err
    assert "Invalid access token" in err
