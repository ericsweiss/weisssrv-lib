#!/usr/bin/env python3
"""Confirm each WLAN's pre-shared key on the controller matches the secret store.

Terraform sends the key write-only and never reads it back. Exit 0 parity,
1 mismatch, 2 error. Contract: docs/SCRIPTS.md.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).resolve().parent / "unifi-psk.json"
REQUIRED_CONFIG_KEYS = ("site", "wlans")


def load_config(path: Path) -> dict:
    cfg = json.loads(path.read_text())
    if not isinstance(cfg, dict):
        raise TypeError(f"{path}: top-level must be an object")
    missing = [k for k in REQUIRED_CONFIG_KEYS if k not in cfg]
    if missing:
        raise ValueError(f"{path}: missing {missing}")
    wlans = cfg["wlans"]
    if not isinstance(wlans, dict) or not wlans:
        raise ValueError(f"{path}: wlans must be a non-empty object of SSID -> env var name")
    for ssid, var in wlans.items():
        if not isinstance(var, str) or not var or not var.replace("_", "").isalnum():
            raise ValueError(f"{path}: wlans[{ssid!r}] must name an environment variable")
    return cfg


def wlanconf_url(base: str, site: str) -> str:
    return f"{base.rstrip('/')}/proxy/network/api/s/{site}/rest/wlanconf"


# CRITICAL: the response carries every WLAN's key in `x_passphrase`. Nothing
# from the body is ever printed or logged; only match/mismatch per SSID leaves
# this process.
def fetch_wlans(url: str, api_key: str, verify: bool = True) -> dict[str, str]:
    """SSID -> passphrase, from the controller."""
    req = urllib.request.Request(url, headers={"X-API-KEY": api_key, "Accept": "application/json"})
    context = ssl.create_default_context()
    if not verify:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=30, context=context) as response:
        payload = json.load(response)
    data = payload.get("data")
    if not isinstance(data, list):
        raise TypeError("unexpected response shape for wlanconf")
    keys: dict[str, str] = {}
    for wlan in data:
        if isinstance(wlan, dict) and isinstance(wlan.get("name"), str):
            keys[wlan["name"]] = str(wlan.get("x_passphrase", ""))
    return keys


def compare(live: dict[str, str], wanted: dict[str, str]) -> list[tuple[str, str]]:
    """(SSID, verdict) per configured WLAN: match, mismatch, absent or unset."""
    results = []
    for ssid, expected in wanted.items():
        if ssid not in live:
            results.append((ssid, "absent"))
        elif expected is None:
            results.append((ssid, "unset"))
        elif hmac.compare_digest(live[ssid].encode(), expected.encode()):
            results.append((ssid, "match"))
        else:
            results.append((ssid, "mismatch"))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="UniFi WLAN pre-shared key parity check.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as e:
        print(f"ERROR: {e}")
        return 2

    base = os.environ.get("UNIFI_API_URL", "")
    api_key = os.environ.get("UNIFI_API_KEY", "")
    if not base or not api_key:
        print("ERROR: UNIFI_API_URL / UNIFI_API_KEY must be set")
        return 2
    verify = os.environ.get("UNIFI_ALLOW_INSECURE", "0") not in ("1", "true", "yes")
    if not verify:
        print("WARNING: TLS verification off (UNIFI_ALLOW_INSECURE)", file=sys.stderr)

    wanted = {ssid: os.environ.get(var) for ssid, var in cfg["wlans"].items()}
    unset = [ssid for ssid, value in wanted.items() if not value]
    if unset:
        print(f"ERROR: no secret in the environment for: {', '.join(sorted(unset))}")
        return 2

    try:
        live = fetch_wlans(wlanconf_url(base, cfg["site"]), api_key, verify=verify)
    except (urllib.error.URLError, OSError, TypeError, ValueError, RuntimeError) as e:
        # The exception type, not the body: the body carries the keys.
        print(f"ERROR: UniFi API read failed: {type(e).__name__}")
        return 2

    results = compare(live, wanted)
    for ssid, verdict in results:
        print(f"{ssid}: {verdict}")
    if all(verdict == "match" for _, verdict in results):
        print(f"OK: every configured WLAN on site {cfg['site']} holds its secret-store key.")
        return 0
    print("MISMATCH: re-send the key with a passphrase_version bump, or fix the secret store.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
