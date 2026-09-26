#!/bin/bash
# Install a macOS launchd job that runs update.sh every 30 minutes (and once at login).
# Remove it with scheduler/uninstall.sh. Logs: cache/update.log
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd -P)"  # physical path: launchd cannot read ~/Documents
LABEL="com.vctpredictor.update"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
INTERVAL="${1:-1800}"
mkdir -p "$HOME/Library/LaunchAgents" "$DIR/cache"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$DIR/update.sh</string></array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>StartInterval</key><integer>$INTERVAL</integer>
  <key>RunAtLoad</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>LowPriorityIO</key><true/>
  <key>StandardOutPath</key><string>$DIR/cache/update.log</string>
  <key>StandardErrorPath</key><string>$DIR/cache/update.log</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
</dict>
</plist>
PL
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed $LABEL: runs every $((INTERVAL / 60)) minutes. Log: $DIR/cache/update.log"
