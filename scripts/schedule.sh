#!/usr/bin/env bash
# Install / remove an hourly macOS launchd job. By default it runs `sync`, which pulls
# what the cloud collector (GitHub Actions) gathered. MODE=ingest collects locally instead,
# which only works while the Mac is awake.
#   scripts/schedule.sh install | uninstall | status
set -euo pipefail
LABEL="com.trendcatcher.ingest"
MODE="${MODE:-sync}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

case "${1:-}" in
  install)
    mkdir -p "$ROOT/data/logs" "$(dirname "$PLIST")"
    cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$ROOT/.venv/bin/python</string><string>-m</string><string>trendcatcher</string><string>$MODE</string>
  </array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>StartInterval</key><integer>3600</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$ROOT/data/logs/ingest.log</string>
  <key>StandardErrorPath</key><string>$ROOT/data/logs/ingest.log</string>
</dict></plist>
PLIST
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    echo "installed: runs '$MODE' hourly, logs at $ROOT/data/logs/ingest.log"
    ;;
  uninstall)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "removed"
    ;;
  status)
    launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -E "state|last exit" || echo "not installed"
    ;;
  *) echo "usage: $0 install|uninstall|status"; exit 1 ;;
esac
