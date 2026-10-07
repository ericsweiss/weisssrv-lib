"""Every role download retries a stalled attempt and bounds each one.

A mirror's TLS handshake stalls often enough to fail a whole play, and get_url's
10s default expires mid-handshake. Retrying never relaxes a pinned checksum.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
COLLECTION = REPO / "ansible_collections" / "weisssrv" / "infra"
ROLES = COLLECTION / "roles"

# get_url exists to pull a file over the network, so every call is in scope.
# uri is in scope only when it writes a dest: a uri without one is a liveness
# probe, whose retry budget belongs to the service it waits for.
DOWNLOAD_MODULES = ("ansible.builtin.get_url", "get_url")
URI_MODULES = ("ansible.builtin.uri", "uri")

# Host-local URLs reach no mirror, so a stalled handshake is not the failure.
LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]", "::1")

# A stalled handshake must be retried, not waited out: one attempt stays bounded
# well under the 10m a molecule job allows a whole converge.
MIN_RETRIES = 2
MIN_TIMEOUT = 10
MAX_TIMEOUT = 120

# Downloads known to carry the shape, so the walk cannot quietly stop reaching
# them, keyed by "<path under the collection>: <task name>".
EXPECTED_COVERED = {
    "roles/acme_certs/tasks/main.yml: Download acme.sh release tarball",
    "roles/adguard_home/tasks/main.yml: Download AdGuard Home",
    "roles/apt_signed_repo/tasks/main.yml: Download signing key",
    "roles/k3s/tasks/gpu.yml: "
    "Download the NVIDIA cuda-keyring package (SHA256 verified before install)",
    "roles/k3s/tasks/install-script.yml: Download k3s install script",
    "roles/proxmox_vm/tasks/main.yml: Download cloud image if not present",
    "roles/proxmox_vm/tasks/main.yml: "
    "Download the VirtIO driver ISO to the Proxmox ISO store",
    "roles/prometheus_exporter/tasks/install.yml: "
    "Download artifact for {{ prometheus_exporter_name }}",
    "roles/restic_offsite/tasks/main.yml: Download the pinned rclone release deb",
    "roles/tailscale/tasks/configure.yml: Download Tailscale GPG key to staging path",
}

# Downloads whose bytes are pinned, keyed like EXPECTED_COVERED. A key fetch is
# absent on purpose: a GPG key is verified by fingerprint, not by checksum.
MUST_PIN_A_CHECKSUM = {
    "roles/acme_certs/tasks/main.yml: Download acme.sh release tarball",
    "roles/k3s/tasks/gpu.yml: "
    "Download the NVIDIA cuda-keyring package (SHA256 verified before install)",
    "roles/proxmox_vm/tasks/main.yml: Download cloud image if not present",
    "roles/proxmox_vm/tasks/main.yml: "
    "Download the VirtIO driver ISO to the Proxmox ISO store",
    "roles/restic_offsite/tasks/main.yml: Download the pinned rclone release deb",
}

# Downloads that genuinely cannot retry, keyed like EXPECTED_COVERED with the
# reason. A reason is mandatory: an unexplained entry hides the trap the gate
# exists to catch, and "not done yet" is not a reason.
CANNOT_RETRY: dict[str, str] = {}


def _flatten(tasks: object) -> list[dict]:
    """Every task in file order, descending into block/rescue/always."""
    flat: list[dict] = []
    if not isinstance(tasks, list):
        return flat
    for task in tasks:
        if not isinstance(task, dict):
            continue
        flat.append(task)
        for key in ("block", "rescue", "always"):
            flat += _flatten(task.get(key))
    return flat


def _role_tasks() -> list[tuple[str, dict]]:
    """(path under the collection, task) for every role task and handler."""
    found: list[tuple[str, dict]] = []
    paths: list[Path] = []
    for suffix in ("yml", "yaml"):
        paths += ROLES.glob(f"*/tasks/**/*.{suffix}")
        paths += ROLES.glob(f"*/handlers/**/*.{suffix}")
    for path in sorted(set(paths)):
        label = path.relative_to(COLLECTION).as_posix()
        found += [(label, task) for task in _flatten(yaml.safe_load(path.read_text()))]
    return found


def _is_local(url: str) -> bool:
    return any(host in url for host in LOCAL_HOSTS)


def _downloads(tasks: list[tuple[str, dict]]) -> list[tuple[str, dict, dict]]:
    """(path, task, module args) for each task downloading a file over http(s)."""
    found = []
    for label, task in tasks:
        for module in DOWNLOAD_MODULES + URI_MODULES:
            args = task.get(module)
            if not isinstance(args, dict):
                continue
            url = str(args.get("url", ""))
            if module in URI_MODULES and not args.get("dest"):
                continue
            if _is_local(url):
                continue
            if f"{label}: {task.get('name', '<unnamed>')}" in CANNOT_RETRY:
                continue
            found.append((label, task, args))
    return found


def _as_int(value: object) -> int | None:
    """The literal int, or None for an absent or templated value."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


TASKS = _role_tasks()
DOWNLOADS = _downloads(TASKS)


def _ids(downloads: list[tuple[str, dict, dict]]) -> list[str]:
    return [f"{label}: {task.get('name', '<unnamed>')}" for label, task, _ in downloads]


def test_the_known_downloads_are_still_in_scope() -> None:
    """The walk must keep reaching the downloads the contract was written for."""
    missing = EXPECTED_COVERED - set(_ids(DOWNLOADS))
    assert not missing, (
        f"the walk no longer reaches {sorted(missing)} — a renamed task is fine, "
        "a download that stopped being covered is not"
    )


def test_every_allowlisted_download_states_why_it_cannot_retry() -> None:
    for key, reason in CANNOT_RETRY.items():
        assert reason.strip(), f"{key} is allowlisted with no reason"


@pytest.mark.parametrize(("label", "task", "args"), DOWNLOADS, ids=_ids(DOWNLOADS))
def test_every_download_retries_a_stalled_attempt(
    label: str, task: dict, args: dict
) -> None:
    name = task.get("name", "<unnamed>")
    register = task.get("register")
    assert register, f"{label}: {name} has no register, so until cannot read it"
    retries = _as_int(task.get("retries"))
    assert retries is not None and retries >= MIN_RETRIES, (
        f"{label}: {name} downloads over the network without literal retries "
        f">= {MIN_RETRIES} — one stalled handshake fails the play"
    )
    delay = _as_int(task.get("delay"))
    assert delay is not None and delay >= 1, (
        f"{label}: {name} retries with no delay, so three attempts hit the same "
        "stalled connection"
    )
    until = str(task.get("until", ""))
    assert register in until, (
        f"{label}: {name} has until {until!r}, which does not read its own "
        f"register {register!r}"
    )


@pytest.mark.parametrize(("label", "task", "args"), DOWNLOADS, ids=_ids(DOWNLOADS))
def test_every_download_bounds_one_attempt(label: str, task: dict, args: dict) -> None:
    """An unbounded attempt hangs the job; the 10s default expires mid-handshake."""
    name = task.get("name", "<unnamed>")
    timeout = _as_int(args.get("timeout"))
    assert timeout is not None, (
        f"{label}: {name} leaves the module's 10s default timeout, which expires "
        "mid-handshake against a loaded mirror"
    )
    assert MIN_TIMEOUT < timeout <= MAX_TIMEOUT, (
        f"{label}: {name} sets timeout {timeout}, outside "
        f"{MIN_TIMEOUT}-{MAX_TIMEOUT}s"
    )


def test_no_download_trades_its_checksum_for_a_retry() -> None:
    """Retrying is about reaching the mirror, never about what gets installed."""
    for label, task, args in DOWNLOADS:
        if "checksum" not in args:
            continue
        assert str(args["checksum"]).strip(), (
            f"{label}: {task.get('name')} carries an empty checksum, so a retry "
            "installs whatever the mirror serves"
        )
    pinned = {
        f"{label}: {task.get('name', '<unnamed>')}"
        for label, task, args in DOWNLOADS
        if "checksum" in args
    }
    assert MUST_PIN_A_CHECKSUM <= pinned, (
        f"{sorted(MUST_PIN_A_CHECKSUM - pinned)} stopped pinning a checksum"
    )


def _fixture(body: str) -> list[tuple[str, dict, dict]]:
    return _downloads([("fixture", yaml.safe_load(body)[0])])


def test_a_download_without_the_contract_is_reported() -> None:
    bare = _fixture(
        "- name: Fetch\n"
        "  ansible.builtin.get_url:\n"
        "    url: https://example.invalid/x.tar.gz\n"
        "    dest: /tmp/x.tar.gz\n"
    )
    assert len(bare) == 1
    with pytest.raises(AssertionError):
        test_every_download_retries_a_stalled_attempt(*bare[0])
    with pytest.raises(AssertionError):
        test_every_download_bounds_one_attempt(*bare[0])


def test_a_retry_reading_the_wrong_register_is_reported() -> None:
    wrong = _fixture(
        "- name: Fetch\n"
        "  ansible.builtin.get_url:\n"
        "    url: https://example.invalid/x.tar.gz\n"
        "    dest: /tmp/x.tar.gz\n"
        "    timeout: 60\n"
        "  register: mine\n"
        "  until: other is succeeded\n"
        "  retries: 3\n"
        "  delay: 5\n"
    )
    with pytest.raises(AssertionError):
        test_every_download_retries_a_stalled_attempt(*wrong[0])


def test_a_templated_retry_count_is_reported() -> None:
    """retries stay literals, so a role variable cannot lower them to zero."""
    templated = _fixture(
        "- name: Fetch\n"
        "  ansible.builtin.get_url:\n"
        "    url: https://example.invalid/x.tar.gz\n"
        "    dest: /tmp/x.tar.gz\n"
        "    timeout: 60\n"
        "  register: mine\n"
        "  until: mine is succeeded\n"
        "  retries: '{{ some_role_retries }}'\n"
        "  delay: 5\n"
    )
    with pytest.raises(AssertionError):
        test_every_download_retries_a_stalled_attempt(*templated[0])


def test_a_uri_download_is_in_scope_but_a_probe_is_not() -> None:
    download = _fixture(
        "- name: Fetch\n"
        "  ansible.builtin.uri:\n"
        "    url: https://example.invalid/x.tar.gz\n"
        "    dest: /tmp/x.tar.gz\n"
    )
    assert len(download) == 1
    probe = _fixture(
        "- name: Wait\n"
        "  ansible.builtin.uri:\n"
        "    url: http://localhost:9100/metrics\n"
        "    status_code: 200\n"
    )
    assert probe == []


def test_an_allowlisted_download_leaves_the_walk() -> None:
    key = "fixture: Fetch"
    CANNOT_RETRY[key] = "fixture"
    try:
        assert (
            _fixture(
                "- name: Fetch\n"
                "  ansible.builtin.get_url:\n"
                "    url: https://example.invalid/x.tar.gz\n"
                "    dest: /tmp/x.tar.gz\n"
            )
            == []
        )
    finally:
        del CANNOT_RETRY[key]
