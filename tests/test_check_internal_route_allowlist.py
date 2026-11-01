"""Tests for scripts/check-internal-route-allowlist.py."""
from __future__ import annotations

import io

import pytest
from script_loader import load_script

mod = load_script("check-internal-route-allowlist.py")

REQUIRED = (
    "--required-middleware", "traefik/lan-tailscale-only",
    "--required-middleware", "traefik/lan-tailscale-strict",
)
DOMAIN = ("--internal-domain", "internal.example")


def _run(stdin_text: str, monkeypatch, *argv: str) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    try:
        return mod.main(list(argv))
    except SystemExit as exc:  # the shared corpus loader exits 2 directly
        return int(exc.code)


INTERNAL = """
---
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata: {name: demo-internal, namespace: apps}
spec:
  entryPoints: [websecure]
  routes:
    - match: Host(`demo.${cluster_internal_domain}`)
      kind: Rule
      middlewares:
        - name: lan-tailscale-only
          namespace: traefik
        - name: hsts-header
          namespace: traefik
      services:
        - name: demo
          port: 80
"""

EXTERNAL = """
---
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata: {name: demo, namespace: apps}
spec:
  routes:
    - match: Host(`demo.public.example`)
      middlewares:
        - name: hsts-header
          namespace: traefik
      services:
        - name: demo
          port: 80
"""

ALLOWLIST = """        - name: lan-tailscale-only
          namespace: traefik
"""
BARE = INTERNAL.replace(ALLOWLIST, "")
HOST = "Host(`demo.${cluster_internal_domain}`)"


def _via(ref: str) -> str:
    """INTERNAL with its allowlist reference replaced by `ref`, namespace-less."""
    return INTERNAL.replace(ALLOWLIST, f"        - name: {ref}\n")


def _bare(match: str, name: str = "catch-all") -> str:
    """BARE renamed, routing on `match` instead of the internal host."""
    return BARE.replace("demo-internal", name).replace(HOST, match)


def _tcp(match: str, fenced: bool = False) -> str:
    middlewares = "      middlewares:\n" + ALLOWLIST if fenced else ""
    return f"""
---
apiVersion: traefik.io/v1alpha1
kind: IngressRouteTCP
metadata: {{name: ssh, namespace: git}}
spec:
  entryPoints: [gitssh]
  routes:
    - match: {match}
{middlewares}      services:
        - name: ssh
          port: 22
"""


CHAIN = """
---
apiVersion: traefik.io/v1alpha1
kind: Middleware
metadata: {name: internal-chain, namespace: apps}
spec:
  chain:
    middlewares:
      - name: lan-tailscale-only
        namespace: traefik
"""
CYCLE = """
---
apiVersion: traefik.io/v1alpha1
kind: Middleware
metadata: {name: chain-a, namespace: apps}
spec:
  chain:
    middlewares:
      - name: chain-b
---
apiVersion: traefik.io/v1alpha1
kind: Middleware
metadata: {name: chain-b, namespace: apps}
spec:
  chain:
    middlewares:
      - name: chain-a
"""
SECOND_ENTRY = """    - match: Host(`admin.${cluster_internal_domain}`) && PathPrefix(`/api`)
      services:
        - name: demo
          port: 80
"""
CRD_REQUIRED = ("--required-middleware", "traefik-lan-tailscale-only@kubernetescrd")
NO_HOST = "routes[0] (no host matcher"


CASES = [
    # (id, corpus, flags, exit code, needle in the output)
    ("allowlisted", INTERNAL + EXTERNAL, (*DOMAIN, *REQUIRED), 0, "1 internal route(s)"),
    ("bare", BARE + EXTERNAL, (*DOMAIN, *REQUIRED), 1, "apps/IngressRoute/demo-internal routes[0]"),
    ("bare-names-what-it-has", BARE, (*DOMAIN, *REQUIRED), 1, "has: traefik/hsts-header"),
    ("literal-domain-any-case", BARE.replace("${cluster_internal_domain}", "Internal.Example"),
     (*DOMAIN, *REQUIRED), 1, None),
    ("apex-host", BARE.replace("demo.${cluster_internal_domain}", "internal.example"),
     (*DOMAIN, *REQUIRED), 1, "routes[0] (internal.example)"),
    ("host-header", BARE.replace(HOST, "HostHeader(`a.internal.example`)"),
     (*DOMAIN, *REQUIRED), 1, "routes[0] (a.internal.example)"),
    ("double-quoted-host", BARE.replace(HOST, 'Host("demo.${cluster_internal_domain}")'),
     (*DOMAIN, *REQUIRED), 1, "demo.${cluster_internal_domain}"),
    ("domain-flag-normalised", BARE.replace("${cluster_internal_domain}", "internal.example"),
     ("--internal-domain", "*.INTERNAL.example", *REQUIRED), 1, "routes[0] (demo.internal.example)"),
    ("lookalike-domain", BARE.replace("${cluster_internal_domain}", "notinternal.example") + INTERNAL,
     (*DOMAIN, *REQUIRED), 0, None),
    ("own-namespace-middleware", _via("lan-tailscale-only"), (*DOMAIN, *REQUIRED), 1, None),
    ("strict-allowlist", INTERNAL.replace("lan-tailscale-only", "lan-tailscale-strict"),
     (*DOMAIN, *REQUIRED), 0, None),
    ("crd-provider-reference", _via("traefik-lan-tailscale-only@kubernetescrd"),
     (*DOMAIN, *REQUIRED), 0, None),
    ("crd-provider-requirement", INTERNAL, (*DOMAIN, *CRD_REQUIRED), 0, None),
    ("crd-provider-requirement-unmet", BARE, (*DOMAIN, *CRD_REQUIRED), 1, None),
    ("chain-with-allowlist", CHAIN + _via("internal-chain"), (*DOMAIN, *REQUIRED), 0, None),
    ("chain-without-allowlist", CHAIN.replace("lan-tailscale-only", "hsts-header") + _via("internal-chain"),
     (*DOMAIN, *REQUIRED), 1, None),
    ("cyclic-chain", CYCLE + _via("chain-a"), (*DOMAIN, *REQUIRED), 1, "has: apps/chain-a"),
    ("one-unfenced-entry", INTERNAL.rstrip("\n") + "\n" + SECOND_ENTRY, (*DOMAIN, *REQUIRED), 1,
     "routes[1]"),
    ("tcp-internal-sni", _tcp("HostSNI(`git.internal.example`)"), (*DOMAIN, *REQUIRED), 1, None),
    ("tcp-internal-sni-fenced", _tcp("HostSNI(`git.internal.example`)", fenced=True),
     (*DOMAIN, *REQUIRED), 0, None),
    # A TCP passthrough takes MiddlewareTCP objects, which no HTTP allowlist satisfies.
    ("tcp-host-less", INTERNAL + _tcp("HostSNI(`*`)"), (*DOMAIN, *REQUIRED), 0,
     "1 internal route(s) and 0 host-less route(s)"),
    ("tcp-host-less-quoted", INTERNAL + _tcp('HostSNI("*")'), (*DOMAIN, *REQUIRED), 0, None),
    ("ingressroute-hostsni-star", INTERNAL + _bare("HostSNI(`*`)"), (*DOMAIN, *REQUIRED), 1, NO_HOST),
    ("regexp-domain", BARE.replace(HOST, r"HostRegexp(`^.+\.internal\.example$`)"),
     (*DOMAIN, *REQUIRED), 1, None),
    ("regexp-lookalike", BARE.replace(HOST, r"HostRegexp(`^.+\.notinternal\.example$`)") + INTERNAL,
     (*DOMAIN, *REQUIRED), 0, None),
    ("regexp-placeholder", _bare(r"HostRegexp(`^.+\.${cluster_internal_domain}$`)", "demo-regexp") + INTERNAL,
     (*DOMAIN, *REQUIRED), 1, r"apps/IngressRoute/demo-regexp routes[0] (^.+\.${cluster_internal_domain}$)"),
    ("no-host-path", INTERNAL + _bare("PathPrefix(`/`)"), (*DOMAIN, *REQUIRED), 1, NO_HOST),
    ("no-host-any-regexp", INTERNAL + _bare("HostRegexp(`.+`)"), (*DOMAIN, *REQUIRED), 1, NO_HOST),
    ("no-host-generic-regexp", INTERNAL + _bare(r"HostRegexp(`^[a-z]+$`)"), (*DOMAIN, *REQUIRED), 1,
     NO_HOST),
    ("no-host-allowlisted", INTERNAL + INTERNAL.replace(HOST, "PathPrefix(`/`)"), (*DOMAIN, *REQUIRED), 0,
     "1 internal route(s) and 1 host-less route(s)"),
    ("no-host-exempt", INTERNAL + _bare("PathPrefix(`/api`)", "traefik-api"),
     (*DOMAIN, *REQUIRED, "--exempt", "apps/IngressRoute/traefik-api=entrypoint has no LoadBalancer"), 0,
     "exempt (entrypoint has no LoadBalancer)"),
    ("external-host-and-path", INTERNAL + EXTERNAL.replace(
        "Host(`demo.public.example`)", "Host(`demo.public.example`) && PathPrefix(`/`)"),
     (*DOMAIN, *REQUIRED), 0, None),
    ("external-regexp", INTERNAL + EXTERNAL.replace(
        "Host(`demo.public.example`)", r"HostRegexp(`^.+\.public\.example$`)"), (*DOMAIN, *REQUIRED), 0, None),
    ("external-placeholder-regexp", INTERNAL + EXTERNAL.replace(
        "Host(`demo.public.example`)", r"HostRegexp(`^.+\.${cluster_external_domain}$`)"),
     (*DOMAIN, *REQUIRED), 0, None),
    ("exempt", BARE, (*DOMAIN, *REQUIRED, "--exempt", "apps/IngressRoute/demo-internal=its own login"), 0,
     "exempt (its own login)"),
    ("no-domain", INTERNAL, REQUIRED, 2, "no domain"),
    ("no-required-middleware", INTERNAL, DOMAIN, 2, "no --required-middleware"),
    ("no-internal-route", EXTERNAL, (*DOMAIN, *REQUIRED), 2, "matches the internal domain"),
    ("empty-corpus", "", (*DOMAIN, *REQUIRED), 2, None),
]


@pytest.mark.parametrize(
    ("corpus", "flags", "code", "needle"), [c[1:] for c in CASES], ids=[c[0] for c in CASES]
)
def test_gate(monkeypatch, capsys, corpus, flags, code, needle):
    assert _run(corpus, monkeypatch, *flags) == code
    if needle:
        captured = capsys.readouterr()
        assert needle in captured.out + captured.err


@pytest.mark.parametrize(
    "match", ["Host(`demo.b.example`)", r"HostRegexp(`^.+\.b\.example$`)"]
)
def test_every_internal_domain_is_checked(monkeypatch, match):
    fenced = INTERNAL.replace("demo.${cluster_internal_domain}", "demo.a.example")
    flags = ("--internal-domain", "a.example", "--internal-domain", "b.example", *REQUIRED)
    assert _run(fenced + _bare(match, "demo-b"), monkeypatch, *flags) == 1


def _cluster_config(tmp_path, data: str):
    config = tmp_path / "cluster-config.yaml"
    config.write_text(f"apiVersion: v1\nkind: ConfigMap\nmetadata: {{name: cluster-config}}\ndata:\n  {data}\n")
    return str(config)


@pytest.mark.parametrize("domain", ["a.example", "b.example"])
def test_a_flag_domain_and_the_cluster_config_domain_both_count(monkeypatch, tmp_path, domain):
    config = _cluster_config(tmp_path, "cluster_internal_domain: b.example")
    corpus = BARE.replace("${cluster_internal_domain}", domain)
    assert _run(corpus, monkeypatch, "--internal-domain", "a.example",
                "--cluster-config", config, *REQUIRED) == 1


def test_a_missing_cluster_config_key_is_an_operator_error(monkeypatch, tmp_path):
    config = _cluster_config(tmp_path, "other: x")
    assert _run(INTERNAL, monkeypatch, "--cluster-config", config, *REQUIRED) == 2


@pytest.mark.parametrize(
    "ref", ["lan-only", "@kubernetescrd", "name@", "ns/", "/name", "a/b/c"]
)
def test_a_malformed_required_middleware_is_an_operator_error(monkeypatch, capsys, ref):
    assert _run(INTERNAL, monkeypatch, *DOMAIN, "--required-middleware", ref) == 2
    assert "NAMESPACE/NAME or NAME@PROVIDER, got" in capsys.readouterr().err


def test_host_args_yield_every_host_with_its_matcher_kind():
    match = 'Host(`a.example`, "b.example") || HostRegexp(`^.+\\.example$`)'
    assert list(mod._host_args(match)) == [
        (False, "a.example"), (False, "b.example"), (True, "^.+\\.example$"),
    ]
