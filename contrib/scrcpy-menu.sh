#!/usr/bin/env bash
#
# scrcpy-menu.sh — interactive menu for setting up / troubleshooting
# Android screen mirroring via scrcpy. Wraps scrcpy-connect.sh so you
# don't need to remember any flags or commands.
#
# Usage: ./scrcpy-menu.sh

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONNECT_SH="$SCRIPT_DIR/scrcpy-connect.sh"
CONF_FILE="$SCRIPT_DIR/.last-connection"

log()  { printf '\033[1;34m[*]\033[0m %s\n' "$1"; }
ok()   { printf '\033[1;32m[OK]\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$1"; }
err()  { printf '\033[1;31m[ERROR]\033[0m %s\n' "$1"; }

pause() { read -rp $'\nPress Enter to continue...' _; }

require_connect_script() {
    if [ ! -x "$CONNECT_SH" ]; then
        err "Missing or non-executable: $CONNECT_SH"
        exit 1
    fi
}

prompt() {
    # prompt "Question" "default"  -> echoes answer
    local question="$1" default="${2:-}" answer
    if [ -n "$default" ]; then
        read -rp "$question [$default]: " answer
        echo "${answer:-$default}"
    else
        read -rp "$question: " answer
        echo "$answer"
    fi
}

saved_target() {
    local t=""
    [ -f "$CONF_FILE" ] && t="$(tr -d '[:space:]' < "$CONF_FILE")"
    [[ "$t" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}:[0-9]{1,5}$ ]] && echo "$t" || true
}
saved_ip()     { saved_target | cut -d: -f1; }
saved_port()   { saved_target | cut -d: -f2; }

# ---- menu actions -----------------------------------------------------------

action_status() {
    "$CONNECT_SH" status
    pause
}

action_pair() {
    echo
    log "On your phone: Settings > Developer options > Wireless debugging > 'Pair device with pairing code'"
    local target code
    target="$(prompt "Enter ip:port shown there")"
    code="$(prompt "Enter the 6-digit pairing code")"
    [ -z "$target" ] || [ -z "$code" ] && { warn "Cancelled — both fields required."; pause; return; }
    "$CONNECT_SH" pair "$target" "$code"
    pause
}

action_connect() {
    echo
    log "On your phone: Settings > Developer options > Wireless debugging (main screen, IP address & Port)"
    local default target
    default="$(saved_target)"
    target="$(prompt "Enter ip:port (leave blank to reuse last saved)" "$default")"
    if [ -z "$target" ]; then
        "$CONNECT_SH" connect
    else
        "$CONNECT_SH" connect "$target"
    fi
    pause
}

action_update_port() {
    echo
    local ip default_port new_port target
    ip="$(saved_ip)"
    if [ -z "$ip" ]; then
        warn "No saved device yet — use 'Connect to a device' first to set the IP."
        pause
        return
    fi
    default_port="$(saved_port)"
    log "Android reassigns the Wireless debugging port each time it's toggled off/on."
    log "On your phone: Settings > Developer options > Wireless debugging (main screen) — read the new port next to the IP."
    echo "  Saved IP: $ip"
    new_port="$(prompt "Enter the new port" "$default_port")"
    [ -z "$new_port" ] && { warn "Cancelled — no port entered."; pause; return; }
    target="$ip:$new_port"
    log "Reconnecting with updated port: $target"
    "$CONNECT_SH" connect "$target"
    pause
}

action_reset() {
    echo
    warn "Full reset: disconnects all devices, restarts adb, and forgets the saved target."
    log "On your phone: Settings > Developer options > Wireless debugging (main screen) for IP & port."
    local ip port ans
    ip="$(prompt "New phone IP" "$(saved_ip)")"
    port="$(prompt "New port" "$(saved_port)")"
    [ -z "$ip" ] || [ -z "$port" ] && { warn "Cancelled — IP and port required."; pause; return; }
    read -rp "Re-pair the phone first? [y/N]: " ans
    if [[ "$ans" =~ ^[Yy] ]]; then
        local ptarget code
        ptarget="$(prompt "Pairing ip:port (from 'Pair device with pairing code')")"
        code="$(prompt "6-digit pairing code")"
        if [ -n "$ptarget" ] && [ -n "$code" ]; then
            "$CONNECT_SH" pair "$ptarget" "$code"
        else
            warn "Skipping pairing — both fields required."
        fi
    fi
    if "$CONNECT_SH" reset "$ip" "$port"; then
        read -rp "Start mirroring now? [Y/n]: " ans
        [[ "$ans" =~ ^[Nn] ]] || "$CONNECT_SH" start
    fi
    pause
}

action_start() {
    echo
    local extra
    extra="$(prompt "Extra scrcpy flags (leave blank for none, e.g. --max-size 1024)")"
    if [ -n "$extra" ]; then
        # shellcheck disable=SC2086
        "$CONNECT_SH" start $extra
    else
        "$CONNECT_SH" start
    fi
    pause
}

action_restart_adb() {
    "$CONNECT_SH" restart-adb
    pause
}

action_full_setup() {
    echo
    log "Full first-time setup wizard"
    echo "You'll need your phone's Wireless debugging screen open:"
    echo "  Settings > Developer options > Wireless debugging"
    echo

    read -rp "Have you already paired this phone before? [y/N]: " already_paired
    if [[ ! "$already_paired" =~ ^[Yy] ]]; then
        action_pair
    fi

    action_connect
    action_start
}

show_menu() {
    clear
    echo "=================================================="
    echo "   scrcpy — Android Screen Mirroring Helper"
    echo "=================================================="
    local saved
    saved="$(saved_target)"
    if [ -n "$saved" ]; then
        echo "  Last saved device: $saved"
    else
        echo "  Last saved device: (none yet)"
    fi
    echo "--------------------------------------------------"
    echo "  1) Full first-time setup (pair + connect + start)"
    echo "  2) Pair a device (first time / after reset)"
    echo "  3) Connect to a device"
    echo "  4) Update port (same IP, Wi-Fi debugging toggled off/on)"
    echo "  5) Start mirroring (scrcpy)"
    echo "  6) Show status / troubleshoot info"
    echo "  7) Restart adb server (fix stuck connections)"
    echo "  8) Full reset (new IP + port)"
    echo "  9) Quit"
    echo "=================================================="
}

main() {
    require_connect_script
    while true; do
        show_menu
        choice="$(prompt "Choose an option")"
        case "$choice" in
            1) action_full_setup ;;
            2) action_pair ;;
            3) action_connect ;;
            4) action_update_port ;;
            5) action_start ;;
            6) action_status ;;
            7) action_restart_adb ;;
            8) action_reset ;;
            9|q|Q) echo "Bye!"; exit 0 ;;
            *) warn "Invalid choice."; pause ;;
        esac
    done
}

main "$@"
