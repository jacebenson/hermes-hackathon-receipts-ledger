#!/usr/bin/env bash
# Install receipt-ledger into the local Hermes profile.
# Copies the desktop half and the backend half, then optionally enables it.
set -euo pipefail

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENABLE=0
[[ "${1:-}" == "--enable" ]] && ENABLE=1

DESKTOP_DIR="$HERMES_HOME/desktop-plugins/receipt-ledger"
PLUGIN_DIR="$HERMES_HOME/plugins/receipt-ledger"

mkdir -p "$DESKTOP_DIR" "$PLUGIN_DIR"
cp "$HERE/desktop/plugin.js" "$DESKTOP_DIR/plugin.js"
# Replace the backend wholesale so a renamed or deleted module never lingers.
rm -rf "$PLUGIN_DIR/dashboard" "$PLUGIN_DIR/jurisdictions"
cp -r "$HERE/dashboard" "$PLUGIN_DIR/dashboard"   # includes dashboard/manifest.json
cp -r "$HERE/jurisdictions" "$PLUGIN_DIR/jurisdictions"
cp "$HERE/__init__.py" "$PLUGIN_DIR/__init__.py"
cp "$HERE/plugin.yaml" "$PLUGIN_DIR/plugin.yaml"
rm -rf "$PLUGIN_DIR/dashboard/__pycache__"

echo "desktop half -> $DESKTOP_DIR/plugin.js"
echo "backend half -> $PLUGIN_DIR"
echo "settings stay in $PLUGIN_DIR/data/ (never copied or overwritten by this script)"

if [[ "$ENABLE" -eq 1 ]]; then
  CONFIG="$HERMES_HOME/config.yaml"
  if grep -q "^\s*-\s*receipt-ledger\s*$" "$CONFIG"; then
    echo "already enabled in $CONFIG"
  else
    cp "$CONFIG" "$CONFIG.bak-receipt-ledger-$(date +%s)"
    python3 - "$CONFIG" <<'PY'
import re, sys
path = sys.argv[1]
text = open(path).read()
# Insert under the existing plugins.enabled list; refuse to guess if the shape differs.
match = re.search(r"(^plugins:\n(?:.*\n)*?^\s{2}enabled:\n)((?:^\s{4}-\s.*\n)*)", text, re.M)
if not match:
    sys.exit("could not find plugins.enabled in config.yaml - add '- receipt-ledger' by hand")
entry = "    - receipt-ledger\n"
if "- receipt-ledger" in match.group(2):
    sys.exit(0)
text = text[: match.end(2)] + entry + text[match.end(2) :]
open(path, "w").write(text)
PY
    echo "enabled receipt-ledger in $CONFIG"
  fi
fi

cat <<'EOS'

Next: reload desktop plugins in the app (Settings -> Plugins, or restart Hermes).
If the backend half is not enabled, the page says so instead of failing silently.
EOS
