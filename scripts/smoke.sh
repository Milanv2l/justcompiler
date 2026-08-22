#!/usr/bin/env bash
# Autonomous-mode smoke suite: run proven real-world repos headless and assert
# on exit codes + harvested artifacts. Usage: scripts/smoke.sh [name ...]
set -u
cd "$(dirname "$0")/.."
OUT_ROOT="./EXECUTABLE"
mkdir -p "$OUT_ROOT"

SPECS=(
  "rich|https://github.com/Textualize/rich|"
  "fmt|https://github.com/fmtlib/fmt|"
  "vite|https://github.com/vuejs/vite|"
  "alacritty|https://github.com/alacritty/alacritty|"
  "syncthing|https://github.com/syncthing/syncthing|"
  "cnnf|https://github.com/Create-Nuclear-Team/CreateNuclearNeoForge|V2"
)
TIMEOUT_SECS="${SMOKE_TIMEOUT:-1500}"
ONLY="${*:-}"
SUMMARY_FILE=$(mktemp)
trap 'rm -f "$SUMMARY_FILE"' EXIT

pass=0; fail=0; skipped_upstream=0
for spec in "${SPECS[@]}"; do
  IFS='|' read -r name url branch <<<"$spec"
  if [[ -n "$ONLY" && ! " $ONLY " == *" $name "* ]]; then continue; fi

  args=(--build "$url")
  [[ -n "$branch" ]] && args+=(--branch "$branch")

  start=$(date +%s)
  timeout "$TIMEOUT_SECS" python3 justcompiler.py "${args[@]}" > "$SUMMARY_FILE" 2>/dev/null
  rc=$?
  dur=$(( $(date +%s) - start ))

  read -r status errclass < <(python3 -c '
import re, sys, json
text = open(sys.argv[1], errors="replace").read()
m = list(re.finditer(r"\{\s*\"status\".*?\n\}", text, re.S))
d = {}
if m:
    try: d = json.loads(m[-1].group(0))
    except Exception: pass
print(d.get("status", "none"), d.get("error_class", "-"))' "$SUMMARY_FILE" 2>/dev/null || true)

  verdict=""
  case "$rc" in
    0) verdict="PASS"; pass=$((pass+1));;
    3) verdict="PARTIAL"; pass=$((pass+1));;
    *)
      if [[ "$errclass" == "upstream_outage" ]]; then
        verdict="SKIP(upstream)"; skipped_upstream=$((skipped_upstream+1))
      else
        verdict="FAIL"; fail=$((fail+1))
      fi;;
  esac
  printf "%-12s rc=%-4s %-14s %-16s %ss\n" "$name" "$rc" "$status" "$verdict" "$dur"
done

echo "-----------------------------------------"
echo "pass=$pass skip=$skipped_upstream fail=$fail"
exit $((fail > 0 ? 1 : 0))
