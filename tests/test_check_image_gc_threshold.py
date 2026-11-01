"""Coverage for check-image-gc-threshold.py.

Drift at each of the three sites fails, an inhibitor that mutes the alert for
good fails, and every unreadable or missing subject exits 2 with a reason.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from script_loader import SCRIPTS

SCRIPT = SCRIPTS / "check-image-gc-threshold.py"

K3S_VARS = """\
k3s_flannel_backend: "wireguard-native"

k3s_kubelet_args:
  - "image-gc-high-threshold=70"
  - "image-gc-low-threshold=50"
"""

RULES = """\
---
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: homelab-storage
spec:
  groups:
    - name: homelab.storage
      rules:
        - alert: DiskUsageWarning
          expr: node_filesystem_used_percent > 80
        # The k3s node root fs is also the kubelet image filesystem. Above the
        # 70% image-gc-high-threshold the kubelet GCs continuously, which
        # DiskUsageWarning at 80% catches too late.
        - alert: KubeletImageGCIneffective
          expr: >-
            100 * (1 - node_filesystem_avail_bytes{mountpoint="/"}
            / node_filesystem_size_bytes{mountpoint="/"}) > 70
          for: 30m
          annotations:
            description: >-
              Root filesystem is {{ $value | printf "%.1f" }}% used, above the
              kubelet image-gc-high-threshold (70%).
"""

AM_CONFIG = """\
---
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: alertmanager-config
spec:
  target:
    template:
      data:
        alertmanager.yaml: |
          global:
            smtp_auth_password: {{ .smtpPassword | quote }}
          inhibit_rules:
            - source_matchers:
                - alertname="DiskUsageWarning"
              target_matchers:
                - alertname="KubeletImageGCIneffective"
              equal:
                - instance
                - mountpoint
"""

# The cluster template's shape: the rule inside HelmRelease values, an alert
# five points above the watermark, and one `of N%` prose mention.
HELM_RELEASE = """\
---
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: kube-prometheus-stack
spec:
  values:
    additionalPrometheusRulesMap:
      storage:
        groups:
          - name: storage
            rules:
              - alert: DiskUsageWarning
                expr: node_filesystem_used_percent > 80
              - alert: KubeletImageGCIneffective
                expr: >-
                  100 * (1 - node_filesystem_avail_bytes{mountpoint="/"}
                  / node_filesystem_size_bytes{mountpoint="/"}) > 75
                annotations:
                  description: >-
                    Root filesystem is at or above the kubelet image-gc-high-threshold
                    of 70%: image GC is running and cannot reach its low watermark.
"""

RULES_PATH = "kubernetes/infrastructure/observability/rules/storage.yaml"
VARS_PATH = "ansible/inventories/prod/group_vars/k3s.yml"
AM_PATH = (
    "kubernetes/infrastructure/observability/kube-prometheus-stack/alertmanager-config.yaml"
)
RELEASE_PATH = "kubernetes/infrastructure/observability/kube-prometheus-stack/release.yaml"
TEMPLATE_ARGS = (
    "--rules", RELEASE_PATH, "--alert-offset", "5", "--alert-cap", "79",
    "--min-prose-mentions", "1",
)

# DiskUsageWarning at 65%, under the alert it inhibits.
MUTED = RULES.replace("node_filesystem_used_percent > 80", "node_filesystem_used_percent > 65")
# MUTED with a severity label on both alerts.
LABELLED = MUTED.replace(
    "          expr: node_filesystem_used_percent > 65\n",
    "          expr: node_filesystem_used_percent > 65\n          labels:\n            severity: warning\n",
).replace("          for: 30m\n", "          for: 30m\n          labels:\n            severity: warning\n")


def _plain(*targets: str, source: str = 'alertname="DiskUsageWarning"', extra: str = "") -> str:
    """A plain Alertmanager config: `source` inhibits through `targets`."""
    quoted = ", ".join(f"'{t}'" for t in targets)
    return f"inhibit_rules:\n  - source_matchers: ['{source}']\n    target_matchers: [{quoted}]\n{extra}"


def _legacy(source: str, target: str) -> str:
    """AM_CONFIG with its matcher lists swapped for legacy maps."""
    return AM_CONFIG.replace(
        "            - source_matchers:\n                - alertname=\"DiskUsageWarning\"\n"
        "              target_matchers:\n                - alertname=\"KubeletImageGCIneffective\"\n",
        f"            - {source}:\n                alertname: {target[0]}\n"
        f"              {target[1]}:\n                alertname: {target[2]}\n",
    )


def _target(matcher: str) -> str:
    return AM_CONFIG.replace('alertname="KubeletImageGCIneffective"', matcher)


def _source(matcher: str) -> str:
    return AM_CONFIG.replace('alertname="DiskUsageWarning"', matcher)


def _write(root: Path, k3s_vars=K3S_VARS, rules=RULES, am_config=AM_CONFIG) -> Path:
    """The three subject files; a None body leaves its directory empty."""
    for rel, body in ((VARS_PATH, k3s_vars), (RULES_PATH, rules), (AM_PATH, am_config)):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        if body is not None:
            (root / rel).write_text(body)
    return root


def _run(root: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo-root", str(root), *extra],
        check=False, capture_output=True, text=True,
    )


NEVER = "can never notify"

CASES = [
    # (id, subject overrides, extra args, exit code, stderr needle)
    ("in-sync", {}, (), 0, None),
    ("ge-expr", {"rules": RULES.replace(") > 70", ") >= 70")}, (), 0, None),
    ("watermark-bumped", {"k3s_vars": K3S_VARS.replace("threshold=70", "threshold=75")}, (), 1, "75"),
    ("expr-bumped", {"rules": RULES.replace(") > 70", ") > 75")}, (), 1,
     "KubeletImageGCIneffective fires above 75%"),
    ("description-left", {"rules": RULES.replace("threshold (70%)", "threshold (65%)")}, (), 1, "65%"),
    ("comment-left", {"rules": RULES.replace("# 70% image", "# 65% image")}, (), 1, "65%"),
    ("no-kubelet-arg", {"k3s_vars": K3S_VARS.replace('  - "image-gc-high-threshold=70"\n', "")},
     (), 2, "sets no image-gc-high-threshold"),
    ("renamed-args-var", {"k3s_vars": K3S_VARS.replace("k3s_kubelet_args", "kubelet_extra_args")},
     (), 2, "no k3s_kubelet_args entries"),
    ("args-var-flag", {"k3s_vars": K3S_VARS.replace("k3s_kubelet_args", "kubelet_extra_args")},
     ("--kubelet-args-var", "kubelet_extra_args"), 0, None),
    ("no-alert", {"rules": RULES.replace("alert: KubeletImageGC", "alert: SomethingElse")}, (), 2,
     "no KubeletImageGCIneffective alert"),
    ("no-comparison", {"rules": RULES.replace('/ node_filesystem_size_bytes{mountpoint="/"}) > 70',
                                              "vector(1)")}, (), 2, "no trailing"),
    ("prose-dropped", {"rules": RULES.replace("image-gc-high-threshold (70%)", "its watermark")},
     (), 2, "expected at least 2"),
    ("prose-floor-flag", {"rules": RULES.replace("image-gc-high-threshold (70%)", "its watermark")},
     ("--min-prose-mentions", "1"), 0, None),
    ("no-rules-file", {"rules": None}, (), 2, "no rule files"),
    ("named-rules-missing", {}, ("--rules", RULES_PATH, "--rules", "nowhere.yaml"), 2,
     "nowhere.yaml: no such rules file or directory"),
    ("unparseable-rules", {"rules": RULES + "  bad: [unclosed\n"}, (), 2, "unparseable YAML"),
    ("no-am-config", {"am_config": None}, (), 2, "alertmanager-config.yaml"),
    ("no-inhibit-rules", {"am_config": AM_CONFIG.replace("          inhibit_rules:", "          route:")},
     (), 2, "declares no inhibit_rules"),
    # The inhibitor arm: a source at or below the alert's threshold mutes it for good.
    ("inhibitor-at-threshold", {"rules": RULES.replace("percent > 80", "percent > 70")}, (), 1, NEVER),
    ("inhibitor-undefined", {"am_config": _source('alertname="RenamedWarning"')}, (), 2, "RenamedWarning"),
    ("regex-target", {"rules": MUTED, "am_config": _target(
        'alertname=~"InodeUsageWarning|KubeletImageGCIneffective"')}, (), 1, NEVER),
    ("regex-source", {"rules": MUTED, "am_config": _source('alertname=~"DiskUsage.*"')}, (), 1,
     "DiskUsageWarning fires above 65%"),
    ("regex-source-matches-nothing", {"am_config": _source('alertname=~"Nothing.*"')}, (), 2, "Nothing"),
    ("negated-target", {"rules": MUTED, "am_config": _target('alertname!="Watchdog"')}, (), 1, NEVER),
    ("negated-regex-excludes", {"rules": MUTED, "am_config": _target('alertname!~"Kubelet.*"')},
     (), 0, None),
    ("target-by-severity", {"rules": LABELLED, "am_config": _target('severity="warning"')}, (), 1,
     "DiskUsageWarning fires above 65%"),
    ("target-label-not-carried", {"rules": LABELLED, "am_config": _target('severity="critical"')},
     (), 0, None),
    ("target-pinned-by-expr", {"rules": MUTED, "am_config": _target('mountpoint="/"')}, (), 1, NEVER),
    ("missing-label-reads-empty", {"rules": MUTED, "am_config": _plain(
        'alertname="KubeletImageGCIneffective"', 'namespace!~"kube-.*"')}, (), 1, NEVER),
    ("regex-requires-missing-label", {"rules": MUTED, "am_config": _plain(
        'alertname="KubeletImageGCIneffective"', 'namespace=~".+"')}, (), 0, None),
    ("pair-dropped", {"rules": MUTED, "am_config": _target('alertname="InodeUsageWarning"')},
     (), 0, None),
    ("unparseable-matcher", {"am_config": _target("alertname")}, (), 2, "inhibit_rules[0]"),
    ("non-mapping-rule-skipped", {"am_config": _plain(
        'alertname="KubeletImageGCIneffective"').replace("inhibit_rules:\n", "inhibit_rules:\n  - junk\n")},
     (), 0, None),
    # Legacy maps: each case passes only when the map is actually read.
    ("legacy-source-undefined", {"am_config": _legacy(
        "source_match", ("NotDefinedAnywhere", "target_match", "KubeletImageGCIneffective"))},
     (), 2, "NotDefinedAnywhere"),
    ("legacy-target-elsewhere", {"rules": MUTED, "am_config": _legacy(
        "source_match", ("DiskUsageWarning", "target_match", "InodeUsageWarning"))}, (), 0, None),
    ("legacy-re-target-elsewhere", {"rules": MUTED, "am_config": _legacy(
        "source_match_re", ("'DiskUsage.*'", "target_match_re", "'Inode.*'"))}, (), 0, None),
    ("legacy-not-a-map", {"am_config": AM_CONFIG.replace(
        "            - source_matchers:\n                - alertname=\"DiskUsageWarning\"\n",
        "            - source_match:\n                - DiskUsageWarning\n")}, (), 2,
     "source_match is not a label map"),
    # Equal labels: only a static label that differs rules a pair out.
    ("equal-alertname-same-alert-only", {
        "rules": LABELLED.replace("severity: warning\n", "severity: critical\n", 1),
        "am_config": _plain('severity="warning"', source='severity="critical"',
                            extra="    equal: [alertname]\n")}, (), 0, None),
    ("equal-label-pinned-twice", {
        "rules": MUTED.replace("node_filesystem_used_percent > 65",
                               'node_filesystem_used_percent{mountpoint="/data"} > 65')
        .replace('/ node_filesystem_size_bytes{mountpoint="/"}', '/ node_filesystem_size_bytes{mountpoint="/var"}'),
        "am_config": _plain('alertname="KubeletImageGCIneffective"', extra="    equal: [mountpoint]\n")},
     (), 1, NEVER),
    # An inhibitor whose expr has no trailing threshold is unknown, not skipped.
    ("inhibitor-unthresholded", {"rules": RULES.replace(
        "percent > 80", "percent > 80 unless on() up == 0")}, (), 2,
     "--unthresholded-inhibitor DiskUsageWarning"),
    ("inhibitor-unthresholded-declared", {"rules": RULES.replace(
        "node_filesystem_used_percent > 80", 'kube_node_status_condition{condition="DiskPressure"} == 1')},
     ("--unthresholded-inhibitor", "DiskUsageWarning"), 0, None),
]


@pytest.mark.parametrize(
    ("overrides", "args", "code", "needle"),
    [case[1:] for case in CASES],
    ids=[case[0] for case in CASES],
)
def test_gate(tmp_path, overrides, args, code, needle):
    result = _run(_write(tmp_path, **overrides), *args)
    assert result.returncode == code, result.stdout + result.stderr
    if needle:
        assert needle in result.stderr


def test_an_inhibitor_in_another_rules_file_is_found(tmp_path):
    root = _write(tmp_path, rules=RULES.replace(
        "        - alert: DiskUsageWarning\n          expr: node_filesystem_used_percent > 80\n", ""))
    (root / RULES_PATH).with_name("disk.yaml").write_text(
        "groups:\n  - name: disk\n    rules:\n      - alert: DiskUsageWarning\n"
        "        expr: node_filesystem_used_percent > 65\n"
    )
    result = _run(root)
    assert result.returncode == 1
    assert NEVER in result.stderr


@pytest.mark.parametrize("mountpoint", ["/", "/var"])
def test_a_label_the_expr_pins_to_two_values_is_unknown(tmp_path, mountpoint):
    rules = MUTED.replace('/ node_filesystem_size_bytes{mountpoint="/"}',
                          '/ node_filesystem_size_bytes{mountpoint="/var"}')
    result = _run(_write(tmp_path, rules=rules, am_config=_plain(f'mountpoint="{mountpoint}"')))
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_source_matched_by_severity_reports_only_other_alerts(tmp_path):
    result = _run(_write(tmp_path, rules=LABELLED, am_config=_source('severity="warning"')))
    assert result.returncode == 1
    assert "DiskUsageWarning fires above 65%" in result.stderr
    assert "KubeletImageGCIneffective fires above 70% and inhibits" not in result.stderr


def test_the_lowest_of_several_alert_definitions_is_the_muting_bound(tmp_path):
    """An inhibitor at 75% mutes the copy at 80% but not the one at 70%."""
    second = "        - alert: KubeletImageGCIneffective\n          expr: node_filesystem_used_percent > 80\n"
    rules = RULES.replace("node_filesystem_used_percent > 80", "node_filesystem_used_percent > 75")
    result = _run(_write(tmp_path, rules=rules + second))
    assert result.returncode == 1
    assert "fires above 80% but the kubelet prunes from 70%" in result.stderr
    assert NEVER not in result.stderr


@pytest.mark.parametrize(
    ("release", "watermark", "code", "needle"),
    [
        (HELM_RELEASE, 70, 0, None),
        (HELM_RELEASE.replace(") > 75", ") > 70"), 70, 1, "should fire above 75%"),
        (HELM_RELEASE.replace(") > 75", ") > 79").replace("of 70%", "of 78%"), 78, 0, None),
        (HELM_RELEASE.replace("of 70%", "of 65%"), 70, 1, "65%"),
    ],
    ids=["offset-passes", "offset-enforced", "cap-bounds-offset", "of-prose-left"],
)
def test_the_template_shape(tmp_path, release, watermark, code, needle):
    """The rule inside HelmRelease values, five points over the watermark, capped at 79."""
    root = _write(tmp_path, k3s_vars=K3S_VARS.replace("threshold=70", f"threshold={watermark}"), rules=None)
    (root / RELEASE_PATH).write_text(release)
    result = _run(root, *TEMPLATE_ARGS)
    assert result.returncode == code, result.stdout + result.stderr
    if needle:
        assert needle in result.stderr
