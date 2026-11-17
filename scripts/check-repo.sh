#!/usr/bin/env bash
# Fast repository checks that need no Chromium checkout. Run locally or in CI:
#   scripts/check-repo.sh
# - every entry in patches/series exists, and every patch file is listed (or is one of
#   the macOS-150 extras)
# - every patch is a well-formed unified diff
# - shell scripts parse (bash -n) and pass shellcheck when it is installed
# - Python tools compile and their --help runs
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
fail=0
err() { echo "FAIL: $*"; fail=1; }

# --- patch series ---------------------------------------------------------------
listed=()
while IFS= read -r line; do
  line="${line%%#*}"; line="$(printf '%s' "$line" | tr -d '[:space:]')"
  [ -n "$line" ] || continue
  listed+=("$line")
  [ -f "patches/$line" ] || err "series lists missing patch $line"
done < patches/series
for f in patches/*.patch; do
  name="$(basename "$f")"
  printf '%s\n' "${listed[@]}" | grep -qx "$name" || err "$name is not in patches/series"
done
dups="$(printf '%s\n' "${listed[@]}" | sort | uniq -d)"
[ -z "$dups" ] || err "series lists a patch twice: $dups"
echo "series: ${#listed[@]} patches"

for f in patches/*.patch patches/macos-150/*.patch; do
  grep -q '^+++ ' "$f" || { err "$f has no +++ header"; continue; }
  # Each hunk header must be well formed; patch --dry-run on /dev/null checks syntax
  # without needing the tree.
  if grep -n '^@@' "$f" | grep -vqE '^[0-9]+:@@ -[0-9]+(,[0-9]+)? \+[0-9]+(,[0-9]+)? @@'; then
    err "$f has a malformed hunk header"
  fi
done

# --- docs refer to files that exist ----------------------------------------------
for doc in README.md NOTES.md COVERAGE.md AUDIT.md docs/*.md patches/README.md; do
  [ -f "$doc" ] || continue
  dir="$(dirname "$doc")"
  while IFS= read -r link; do
    case "$link" in http*|\#*|mailto:*) continue ;; esac
    target="${link%%#*}"
    [ -n "$target" ] || continue
    [ -e "$dir/$target" ] || err "$doc links to missing $target"
  done < <(grep -oE '\]\([^)]+\)' "$doc" | sed -E 's/^\]\((.*)\)$/\1/')
done

# --- shell ------------------------------------------------------------------------
for s in scripts/*.sh; do
  bash -n "$s" || err "bash -n $s"
done
if command -v shellcheck >/dev/null; then
  shellcheck -x -S warning -e SC2034,SC2164,SC1091 scripts/*.sh || err "shellcheck"
else
  echo "shellcheck not installed; skipped"
fi

# --- python -----------------------------------------------------------------------
python3 -m py_compile tools/*.py || err "py_compile"
for t in tools/stealth_audit.py tools/fingerprint_selftest.py tools/site_checks.py tools/socks5_udp_proxy.py \
         tools/network_selftest.py tools/fingerprint_library.py tools/camauto.py \
         tools/camauto_selftest.py; do
  [ -f "$t" ] || continue
  [ -x "$t" ] || err "$t is not executable"
  "./$t" --help >/dev/null || err "$t --help"
done

# --- workflows parse ----------------------------------------------------------------
if python3 -c 'import yaml' 2>/dev/null; then
  for w in .github/workflows/*.yml; do
    [ -f "$w" ] || continue
    python3 -c 'import sys, yaml; yaml.safe_load(open(sys.argv[1]))' "$w" || err "yaml $w"
  done
fi

[ "$fail" = 0 ] && echo "OK: repository checks passed"
exit "$fail"
