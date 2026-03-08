#!/bin/zsh
# Install the claude-memory hourly import/distill LaunchAgent on macOS.
# Run once after cloning: bash setup-launchagent.sh
# To uninstall: launchctl unload ~/Library/LaunchAgents/com.claude-memory.import.plist

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_NAME="com.claude-memory.import"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"
CRON_SCRIPT="$SCRIPT_DIR/import-cron.sh"
LOG_DIR="/tmp"

# Ensure the cron script is executable
chmod +x "$CRON_SCRIPT"

echo "Installing LaunchAgent: $PLIST_NAME"
echo "  Script: $CRON_SCRIPT"
echo "  Logs:   $LOG_DIR/claude-memory-import.log"
echo ""

# Write the plist
cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_NAME}</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/zsh</string>
        <string>${CRON_SCRIPT}</string>
    </array>

    <key>StartInterval</key>
    <integer>3600</integer>

    <key>RunAtLoad</key>
    <false/>

    <key>StandardOutPath</key>
    <string>${LOG_DIR}/claude-memory-import.log</string>

    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/claude-memory-import-error.log</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>HOME</key>
        <string>${HOME}</string>
    </dict>
</dict>
</plist>
PLIST

# Unload existing agent if running, then reload
if launchctl list | grep -q "$PLIST_NAME" 2>/dev/null; then
    echo "Unloading existing agent..."
    launchctl unload "$PLIST_PATH" 2>/dev/null || true
fi

launchctl load "$PLIST_PATH"
echo "✅ LaunchAgent loaded. Runs every hour."
echo ""
echo "Useful commands:"
echo "  View logs:    tail -f $LOG_DIR/claude-memory-import.log"
echo "  Run now:      launchctl start $PLIST_NAME"
echo "  Disable:      launchctl unload $PLIST_PATH"
echo "  Re-enable:    launchctl load $PLIST_PATH"
