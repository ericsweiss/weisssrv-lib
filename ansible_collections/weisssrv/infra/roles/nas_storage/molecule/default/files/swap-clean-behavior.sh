#!/usr/bin/env bash
# Molecule behavioural check for the swap-clean helpers that need no swap.
# Sources the rendered script with its entrypoint disabled, so nothing cycles
# swap or stops a guest. Runs on the target via ansible.builtin.script.
set -euo pipefail

SWAPCLEAN="${1:-/usr/local/sbin/swap-clean.sh}"
[ -f "$SWAPCLEAN" ] || { echo >&2 "swap-clean.sh not rendered at $SWAPCLEAN"; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

fail() { echo >&2 "FAIL: $*"; exit 1; }

sed 's/^main "\$@"$/# main disabled for behavior test/' "$SWAPCLEAN" > "$WORK/swap-clean-lib.sh"
grep -q 'main disabled for behavior test' "$WORK/swap-clean-lib.sh" \
  || fail "could not disable the swap-clean entrypoint"

grep -q "skipping without stopping guests" "$SWAPCLEAN" \
  || fail "the escalation does not refuse an unreachable goal"

# Feasibility pre-check: stopping every candidate must reach need_kb, or the
# escalation stops production guests for an abort it can predict. The estimate
# counts live readings only: 1 GiB resident + 256 MiB process swap each.
printf "VmSwap:\t262144 kB\n" > "$WORK/vmswap-status"
env -i bash -c '
  # shellcheck disable=SC1091
  source "'"$WORK"'/swap-clean-lib.sh" || true
  set +e
  W="'"$WORK"'"
  PROC_ROOT="$W/proc"
  mkdir -p "$PROC_ROOT/4242"
  cp "$W/vmswap-status" "$PROC_ROOT/4242/status"
  qm() {
    case "$1" in
      status)
        echo "status: running"
        if [ "${3:-}" = "--verbose" ]; then
          echo "maxmem: 17179869184"
          echo "mem: 1073741824"
          echo "pid: 4242"
        fi
        ;;
      config) echo "memory: 16384" ;;
    esac
  }
  stop_guests="157:immich:120 153:gitlab:300 bogus"
  compute_attainable_headroom 1000
  [ "$stop_candidates" = 2 ] || exit 81
  [ "$attainable_kb" = 2622440 ] || exit 82
  qm() { return 1; }
  compute_attainable_headroom 7
  [ "$stop_candidates" = 0 ] || exit 83
  [ "$attainable_kb" = 7 ] || exit 84
  exit 0
' || fail "attainable-headroom accounting for the escalation pre-check (exit $?)"

# A running candidate with no live reading promises nothing, so a ballooned or
# freshly booted guest cannot make an unreachable target look feasible.
env -i bash -c '
  # shellcheck disable=SC1091
  source "'"$WORK"'/swap-clean-lib.sh" || true
  set +e
  PROC_ROOT="'"$WORK"'/proc-empty"
  qm() {
    case "$1" in
      status)
        echo "status: running"
        if [ "${3:-}" = "--verbose" ]; then echo "maxmem: 17179869184"; fi
        ;;
      config) echo "memory: 16384" ;;
    esac
  }
  [ "$(guest_memory_kb 157)" = 0 ] || exit 85
  [ "$(guest_swap_kb 157)" = 0 ] || exit 86
  stop_guests="157:immich:120"
  compute_attainable_headroom 1000
  [ "$stop_candidates" = 1 ] || exit 87
  [ "$attainable_kb" = 1000 ] || exit 88
  exit 0
' || fail "a candidate with no live reading promised headroom anyway (exit $?)"

# The authoritative cap wins over the pre-run live value: a run killed before
# its restore leaves the live value AT the shrink target, and adopting that
# would ratchet the cap down for good.
env -i bash -c '
  # shellcheck disable=SC1091
  source "'"$WORK"'/swap-clean-lib.sh" || true
  set +e
  PROM_FILE="'"$WORK"'/swap_clean.prom"
  arc_path="'"$WORK"'/arc_max"
  printf "%s\n" 2147483648 > "$arc_path"
  arc_shrunk=1
  arc_orig=2147483648
  arc_restore_bytes=8589934592
  run_success=1
  swap_cleared_bytes=1024
  guests_to_restore=""
  cleanup
  [ "$(cat "$arc_path")" = "8589934592" ] || exit 51
  grep -qx "swap_clean_last_run_success 1" "$PROM_FILE" || exit 52
' || fail "cleanup did not restore the authoritative ARC cap (exit $?)"

# A restore that cannot be verified fails the run instead of leaving the NAS
# pinned at the shrink value with a green metric.
env -i bash -c '
  # shellcheck disable=SC1091
  source "'"$WORK"'/swap-clean-lib.sh" || true
  set +e
  PROM_FILE="'"$WORK"'/swap_clean.prom"
  arc_path="'"$WORK"'/no-such-dir/arc_max"
  arc_shrunk=1
  arc_orig=2147483648
  arc_restore_bytes=8589934592
  run_success=1
  guests_to_restore=""
  cleanup
  grep -qx "swap_clean_last_run_success 0" "$PROM_FILE" || exit 53
' || fail "an unrestorable ARC cap did not demote the run (exit $?)"

# A run that never shrank must leave the live value alone.
env -i bash -c '
  # shellcheck disable=SC1091
  source "'"$WORK"'/swap-clean-lib.sh" || true
  set +e
  PROM_FILE="'"$WORK"'/swap_clean.prom"
  arc_path="'"$WORK"'/arc_untouched"
  printf "%s\n" 12884901888 > "$arc_path"
  arc_shrunk=0
  arc_restore_bytes=8589934592
  run_success=1
  guests_to_restore=""
  cleanup
  [ "$(cat "$arc_path")" = "12884901888" ] || exit 54
  grep -qx "swap_clean_last_run_success 1" "$PROM_FILE" || exit 55
' || fail "cleanup wrote the ARC cap on a run that never shrank it (exit $?)"

# Pre-flight: only an absent dm-crypt mapper is a healthy skip, and any
# conflicting overnight unit skips the night.
env -i bash -c '
  # shellcheck disable=SC1091
  source "'"$WORK"'/swap-clean-lib.sh" || true
  set +e
  W="'"$WORK"'"
  systemctl() { return 1; }
  printf "%s\n" "/dev/mapper/cryptswap none swap sw 0 0" > "$W/fstab-mapper"
  FSTAB="$W/fstab-mapper"
  case "$(preflight_skip_reason)" in
    *"pending activation reboot"*) ;;
    *) exit 61 ;;
  esac
  printf "%s\n" "UUID=0000-absent none swap sw 0 0" > "$W/fstab-uuid"
  FSTAB="$W/fstab-uuid"
  [ -z "$(preflight_skip_reason)" ] || exit 62
  systemctl() { case "$*" in *archive-backup.service*) return 0 ;; *) return 1 ;; esac; }
  case "$(preflight_skip_reason)" in
    *"archive-backup.service active"*) ;;
    *) exit 63 ;;
  esac
' || fail "pre-flight skip decision (exit $?)"

# A skipped night keeps run_success 1, so without its own gauge a permanently
# active conflicting unit would disable swap-clean with every metric green.
env -i bash -c '
  # shellcheck disable=SC1091
  source "'"$WORK"'/swap-clean-lib.sh" || true
  set +e
  PROM_FILE="'"$WORK"'/swap_clean_skip.prom"
  PROM_DIR="'"$WORK"'"
  skip_reason="archive-backup.service active"
  write_prom_metrics 1 0 0 0
  grep -qx "swap_clean_last_run_skipped 1" "$PROM_FILE" || exit 64
  grep -qF "swap_clean_skip_reason_info{reason=\"archive-backup.service active\"} 1"     "$PROM_FILE" || exit 65
  skip_reason=""
  write_prom_metrics 1 4096 0 0
  grep -qx "swap_clean_last_run_skipped 0" "$PROM_FILE" || exit 66
  grep -q "swap_clean_skip_reason_info" "$PROM_FILE" && exit 67
  exit 0
' || fail "the skip gauge does not distinguish a skipped night from a real run (exit $?)"

# fstab and systemd names carry backslash escapes (\040, \x2d). They are not
# valid Prometheus label escapes, so an unsanitized reason voids the whole .prom
# and takes the success gauge and timestamp down with it.
env -i bash -c '
  # shellcheck disable=SC1091
  source "'"$WORK"'/swap-clean-lib.sh" || true
  set +e
  PROM_FILE="'"$WORK"'/swap_clean_escape.prom"
  PROM_DIR="'"$WORK"'"
  skip_reason="dev-mapper-vg\x2dswap.device active"
  write_prom_metrics 1 0 0 0
  grep -qx "swap_clean_last_run_skipped 1" "$PROM_FILE" || exit 71
  line="$(grep "^swap_clean_skip_reason_info" "$PROM_FILE")"
  case "$line" in
    *\\*) exit 72 ;;
  esac
  grep -qF "swap_clean_skip_reason_info{reason=\"dev-mapper-vgx2dswap.device active\"} 1" "$PROM_FILE" || exit 73
  exit 0
' || fail "the skip reason label is not sanitized for Prometheus (exit $?)"

echo "swap-clean ARC restore + pre-flight behavior OK"
