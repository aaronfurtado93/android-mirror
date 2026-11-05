#!/usr/bin/env bash
#
# scrcpy-connect.sh — helper for connecting to an Android phone over wireless
# ADB and launching scrcpy, plus basic troubleshooting.
#
# Usage:
#   ./scrcpy-connect.sh pair <ip>:<port> <code>   Pair with phone (first-time setup, or after Wi-Fi debugging reset)
#   ./scrcpy-connect.sh connect <ip>:<port>       Connect to a previously paired phone (saves it for next time)
#   ./scrcpy-connect.sh connect                   Reconnect using the last saved ip:port
#   ./scrcpy-connect.sh start [scrcpy args...]     Connect (if needed) and launch scrcpy
#   ./scrcpy-connect.sh status                    Show adb devices + whether scrcpy/adb are installed
#   ./scrcpy-connect.sh restart-adb                Kill and restart the adb server (fixes most "stuck" issues)
#   ./scrcpy-connect.sh reset [ip] [port]          Full reset: kill adb, drop all connections, forget saved
#                                                  target, then reconnect to a new ip/port (prompts if omitted)
#   ./scrcpy-connect.sh help                       Show this help
#
# Notes:
#   - Pairing port and connect port are DIFFERENT on Android 11+ Wireless debugging.
#     Pairing port: Settings > Developer options > Wireless debugging > "Pair device with pairing code"
#     Connect port: Settings > Developer options > Wireless debugging (main screen, IP address & Port)
#   - The connect port changes each time Wi-Fi debugging is toggled off/on, so you may
#     need to re-run `connect <ip>:<port>` with a fresh port occasionally. Pairing usually
#     only needs to be done once per device (until you forget/reset it).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF_FILE="$SCRIPT_DIR/.last-connection"

# ---- helpers -----------------------------------------------------------

log()  { printf '\033[1;34m[*]\033[0m %s\n' "$1"; }
ok()   { printf '\033[1;32m[OK]\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$1"; }
err()  { printf '\033[1;31m[ERROR]\033[0m %s\n' "$1"; }

check_deps() {
    local missing=0
    if ! command -v adb >/dev/null 2>&1; then
        err "adb not found. Install it (e.g. 'sudo apt install adb' / 'android-tools')."
        missing=1
    fi
    if ! command -v scrcpy >/dev/null 2>&1; then
        err "scrcpy not found. Install it (e.g. 'sudo apt install scrcpy')."
        missing=1
    fi
    [ "$missing" -eq 0 ]
}

# Valid target = IPv4:port (port 1-65535).
valid_target() {
    local ip port
    [[ "$1" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}:[0-9]{1,5}$ ]] || return 1
    ip="${1%%:*}"; port="${1##*:}"
    local IFS=.; local -a o=($ip)
    local n
    for n in "${o[@]}"; do [ "$n" -le 255 ] || return 1; done
    [ "$port" -ge 1 ] && [ "$port" -le 65535 ]
}

save_conn() { echo "$1" > "$CONF_FILE"; }
# Only returns a saved target if it is well-formed; a corrupt file is ignored.
load_conn() {
    local t=""
    [ -f "$CONF_FILE" ] && t="$(tr -d '[:space:]' < "$CONF_FILE")"
    if [ -n "$t" ] && ! valid_target "$t"; then
        warn "Ignoring malformed saved connection '$t' (expected ip:port)." >&2
        t=""
    fi
    echo "$t"
}

# Disconnect any adb device currently in "offline" state (stale entries left
# behind after the wireless-debugging port changes). Keeps `adb devices` and
# scrcpy from choking on duplicates for the same phone.
cleanup_offline_devices() {
    local stale
    stale="$(adb devices | awk '$2 == "offline" {print $1}')"
    [ -z "$stale" ] && return 0
    while IFS= read -r addr; do
        [ -z "$addr" ] && continue
        log "Removing stale offline device: $addr"
        adb disconnect "$addr" >/dev/null 2>&1 || true
    done <<< "$stale"
}

# Disconnect every wireless (tcpip) adb connection, regardless of state.
# This is a single-phone helper: Android reassigns a new port each time
# Wireless debugging is toggled, leaving the old tcpip entry connected
# (often still shown as "device", not "offline") alongside the new one,
# which makes scrcpy/adb refuse to pick one automatically. Wiping all
# tcpip connections before reconnecting keeps only the fresh one around.
cleanup_wireless_devices() {
    local addrs
    addrs="$(adb devices | awk '$1 ~ /:[0-9]+$/ {print $1}')"
    [ -z "$addrs" ] && return 0
    while IFS= read -r addr; do
        [ -z "$addr" ] && continue
        log "Clearing existing wireless connection: $addr"
        adb disconnect "$addr" >/dev/null 2>&1 || true
    done <<< "$addrs"
}

# ---- commands -----------------------------------------------------------

cmd_status() {
    log "Checking dependencies..."
    if command -v adb >/dev/null 2>&1; then ok "adb: $(adb --version | head -1)"; else err "adb not installed"; fi
    if command -v scrcpy >/dev/null 2>&1; then ok "scrcpy: $(scrcpy --version | head -1)"; else err "scrcpy not installed"; fi

    echo
    cleanup_offline_devices

    log "adb devices:"
    adb devices -l || true

    echo
    local saved
    saved="$(load_conn)"
    if [ -n "$saved" ]; then
        log "Last saved connection: $saved"
    else
        warn "No saved connection yet. Run: $0 connect <ip>:<port>"
    fi
}

cmd_pair() {
    local target="${1:-}" code="${2:-}"
    if [ -z "$target" ] || [ -z "$code" ]; then
        err "Usage: $0 pair <ip>:<port> <pairing-code>"
        echo "  Get these from: Settings > Developer options > Wireless debugging > Pair device with pairing code"
        exit 1
    fi
    check_deps || exit 1
    log "Pairing with $target ..."
    adb pair "$target" "$code"
    ok "If pairing succeeded above, now run: $0 connect <ip>:<port>  (using the port from the MAIN Wireless debugging screen, not the pairing one)"
}

cmd_connect() {
    local target="${1:-}"
    check_deps || exit 1

    if [ -z "$target" ]; then
        target="$(load_conn)"
        if [ -z "$target" ]; then
            err "No ip:port given and no saved connection found."
            echo "  Usage: $0 connect <ip>:<port>"
            exit 1
        fi
        log "No ip:port given, reusing last saved: $target"
    fi

    if ! valid_target "$target"; then
        err "'$target' is not a valid ip:port (e.g. 192.168.1.50:37012)."
        exit 1
    fi

    cleanup_wireless_devices

    log "Connecting to $target ..."
    if adb connect "$target"; then
        save_conn "$target"
    else
        err "adb connect failed."
        echo "  - Make sure the phone and this machine are on the same Wi-Fi network."
        echo "  - Wireless debugging port changes periodically — re-check the main"
        echo "    Wireless debugging screen on the phone for the current port."
        echo "  - If you've never paired this phone, run: $0 pair <ip>:<port> <code>"
        exit 1
    fi

    echo
    log "Current devices:"
    adb devices -l

    if adb devices | awk -v t="$target" '$1 == t && $2 == "device" {f=1} END {exit !f}'; then
        ok "Connected successfully."
    else
        warn "Device shows up but may be 'unauthorized' or 'offline' — check the phone screen for an authorization prompt."
    fi
}

cmd_start() {
    check_deps || exit 1

    # Always reset wireless connections down to a single one before launching
    # scrcpy — leftover duplicate tcpip entries (old + new port after a
    # Wireless debugging toggle) make scrcpy refuse to pick a device.
    local target
    target="$(load_conn)"
    if [ -n "$target" ]; then
        cleanup_wireless_devices
        log "Reconnecting to $target ..."
        if ! adb connect "$target" 2>&1 | grep -q "connected to"; then
            err "Could not reconnect to $target (port probably changed)."
            echo "  Run: $0 reset   to enter a new IP/port."
            exit 1
        fi
    elif ! adb devices | grep -q "device$"; then
        err "No saved connection and no device currently attached."
        echo "  Run: $0 connect <ip>:<port>   (or use the menu's Connect option)"
        exit 1
    fi

    log "Launching scrcpy..."
    scrcpy "$@"
}

cmd_restart_adb() {
    log "Restarting adb server..."
    adb kill-server
    adb start-server
    ok "adb server restarted."
    adb devices -l
}

# Full reset: wipe adb state and saved target, then connect to a new ip/port.
cmd_reset() {
    local ip="${1:-}" port="${2:-}" old_ip old_port target
    check_deps || exit 1

    old_ip="$(load_conn | cut -d: -f1)"
    old_port="$(load_conn | cut -d: -f2)"

    # Allow "reset ip:port" as a single argument.
    if [ -n "$ip" ] && [ -z "$port" ] && [[ "$ip" == *:* ]]; then
        port="${ip##*:}"; ip="${ip%%:*}"
    fi

    log "Resetting: disconnecting all devices and restarting adb..."
    adb disconnect >/dev/null 2>&1 || true
    adb kill-server >/dev/null 2>&1 || true
    rm -f "$CONF_FILE"
    adb start-server >/dev/null 2>&1
    ok "adb reset; saved connection cleared."

    while true; do
        if [ -z "$ip" ]; then
            read -rp "Phone IP address${old_ip:+ [$old_ip]}: " ip
            ip="${ip:-$old_ip}"
        fi
        if [ -z "$port" ]; then
            read -rp "Wireless debugging port${old_port:+ [$old_port]}: " port
            port="${port:-$old_port}"
        fi
        target="$(echo "$ip:$port" | tr -d '[:space:]')"
        if valid_target "$target"; then break; fi
        err "'$target' is not a valid ip:port — try again."
        ip=""; port=""
    done

    cmd_connect "$target"
}

cmd_help() {
    sed -n '2,/^set -/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
}

# ---- main -----------------------------------------------------------

main() {
    local cmd="${1:-help}"
    shift || true
    case "$cmd" in
        pair)         cmd_pair "$@" ;;
        connect)      cmd_connect "$@" ;;
        start)        cmd_start "$@" ;;
        status)       cmd_status "$@" ;;
        restart-adb)  cmd_restart_adb "$@" ;;
        reset)        cmd_reset "$@" ;;
        help|-h|--help) cmd_help ;;
        *)
            err "Unknown command: $cmd"
            cmd_help
            exit 1
            ;;
    esac
}

main "$@"
