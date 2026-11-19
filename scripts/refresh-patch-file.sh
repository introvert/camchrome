#!/usr/bin/env bash
# Fold a hand fix in the source tree back into the patch that owns the file.
#
#   scripts/refresh-patch-file.sh <patch-name> <path/in/src> [<path/in/src> ...]
#
# Rewrites the named files' sections of patches/<patch-name> as the diff between the file before
# that patch (the snapshot rebase-series.sh took, or nothing for a file the patch creates) and the
# file in the tree now. Only for files no later patch in the series touches, otherwise the later
# patches' changes would end up in this one; the script refuses those.
source "$(dirname "$0")/common.sh"
patch_name="$1"; shift
pf="$REPO/patches/$patch_name"
pre="$WORK/rebase/pre/$patch_name"
[ -f "$pf" ] || { echo "no $pf"; exit 1; }

later=0
while IFS= read -r line; do
  line="${line%%#*}"; line="$(printf '%s' "$line" | tr -d '[:space:]')"
  [ -n "$line" ] || continue
  [ "$line" = "$patch_name" ] && { later=1; continue; }
  [ "$later" = 1 ] || continue
  for f in "$@"; do
    if grep -qE "^\+\+\+ b/${f}([[:space:]]|$)" "$REPO/patches/$line"; then
      echo "refusing: $line (later in the series) also touches $f"; exit 1
    fi
  done
done < "$REPO/patches/series"

for f in "$@"; do
  if [ -e "$pre/$f" ]; then a="$pre/$f"; la="a/$f"; else a=/dev/null; la=/dev/null; fi
  diff -u --label "$la" --label "b/$f" "$a" "$SRC/$f" > "$WORK/rebase/section.diff" || true
  python3 - "$pf" "$f" "$WORK/rebase/section.diff" <<'EOF'
import re, sys
pf, path, sec = sys.argv[1], sys.argv[2], open(sys.argv[3]).read()
text = open(pf).read()
# A section runs from its "--- " line (optionally preceded by diff/index lines) to the next one.
parts = re.split(r'(?m)^(?=(?:diff --git .*\n)?(?:index .*\n)?--- )', text)
out, done = [], False
for p in parts:
    if re.search(r'(?m)^\+\+\+ b/' + re.escape(path) + r'(\s|$)', p):
        out.append(sec); done = True
    else:
        out.append(p)
if not done:
    out.append(sec)
open(pf, 'w').write(''.join(out))
print(("replaced " if done else "added ") + path)
EOF
done
