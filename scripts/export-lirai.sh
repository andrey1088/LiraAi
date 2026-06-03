#!/usr/bin/env bash
# Filtered git ls-files snapshot → destination folder for release.
#   ./scripts/export-lirai.sh ~/LiraAi
#   LIRA_EXPORT_DELETE=1  — mirror (remove extra files in dest)
# Import back to Lira2: LIRA_EXPORT_SRC=~/LiraAi LIRA_IMPORT=1 ./scripts/export-lirai.sh ~/Lira2
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${LIRA_EXPORT_SRC:-$ROOT}"
IGNORE="$ROOT/.export-ignore"
DRY_RUN=0
DEST=""

usage() {
  cat >&2 <<'EOF'
Copy tracked files to a folder for review and push (not a daily workspace).

  ./scripts/export-lirai.sh ~/LiraAi
  LIRA_EXPORT_SRC=~/LiraAi LIRA_IMPORT=1 ./scripts/export-lirai.sh ~/Lira2

  LIRA_EXPORT_DELETE=1  — delete dest files not in the snapshot

After export:
  cd <dest> && git add -A && git status
  (config.json must not appear)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *)
      [[ -z "$DEST" ]] || { echo "Extra argument: $1" >&2; usage; exit 1; }
      DEST="$1"
      shift
      ;;
  esac
done

DEST="${DEST:-${LIRA_AI_DEST:-}}"
[[ -n "$DEST" ]] || { usage; exit 1; }

DEST="$(mkdir -p "$DEST" && cd "$(dirname "$DEST")" && pwd)/$(basename "$DEST")"

[[ -f "$IGNORE" ]] || { echo "Missing $IGNORE" >&2; exit 1; }
[[ -d "$SRC/.git" ]] || { echo "Source must be a git repo: $SRC" >&2; exit 1; }

if [[ "${LIRA_IMPORT:-0}" != 1 ]]; then
  if [[ "$DEST" == "$ROOT" || "$DEST" == "$ROOT/"* ]]; then
    echo "Dest cannot be inside source (or set LIRA_IMPORT=1): $DEST" >&2
    exit 1
  fi
fi
if [[ "$DEST" == "$SRC" ]]; then
  echo "Source and dest are the same: $SRC" >&2
  exit 1
fi

FILTER="$ROOT/scripts/export_lirai_filter.py"
LIST="$(mktemp)"
trap 'rm -f "$LIST"' EXIT

[[ -f "$FILTER" ]] || { echo "Missing $FILTER" >&2; exit 1; }

git -C "$SRC" ls-files -z | EXPORT_IGNORE="$IGNORE" python3 "$FILTER" >"$LIST"

COUNT="$(python3 -c "import sys; print(sys.stdin.buffer.read().count(b'\\0'))" <"$LIST")"
if [[ "$COUNT" -eq 0 ]]; then
  echo "Export file list is empty — check .export-ignore" >&2
  exit 1
fi

RSYNC=(rsync -a --files-from="$LIST" --from0)
[[ "$DRY_RUN" == 1 ]] && RSYNC+=(-n)
[[ "${LIRA_EXPORT_DELETE:-0}" == 1 ]] && RSYNC+=(--delete)

echo "Source:      $SRC (git ls-files only, $(( COUNT )) files)"
echo "Destination: $DEST"
[[ "$DRY_RUN" == 1 ]] && echo "(dry-run)"

"${RSYNC[@]}" "$SRC/" "$DEST/"

[[ "$DRY_RUN" == 1 ]] && exit 0

if [[ -f "$DEST/config.json" ]]; then
  echo "ERROR: config.json in dest" >&2
  exit 1
fi

echo ""
echo "Done. Check: cd <dest> && git status (config.json must not appear)."
