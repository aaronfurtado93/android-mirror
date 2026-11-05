#!/usr/bin/env bash
# Android Mirror installer for Linux and macOS.
#
#   ./install.sh                  install (or upgrade) for the current user, no root needed
#   ./install.sh --yes            don't ask questions (installs missing adb/scrcpy the recommended way)
#   ./install.sh --no-deps        skip adb/scrcpy setup (do it later from the web UI or `android-mirror install-deps`)
#   ./install.sh --no-desktop     no app-menu / Applications entry
#   ./install.sh --no-autostart   don't start the server at login
#   ./install.sh --uninstall [--purge]
#
# Works from a source checkout (builds the package) or from a release bundle (installs the bundled wheel).
# Everything goes into your home directory:
#   Linux  ~/.local/share/android-mirror/venv, ~/.local/bin/android-mirror
#   macOS  ~/Library/Application Support/AndroidMirror/venv, ~/.local/bin/android-mirror, ~/Applications/Android Mirror.app
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
OS="$(uname -s)"
YES=0 DEPS=1 DESKTOP=1 AUTOSTART=1 UNINSTALL=0 PURGE=0
for arg in "$@"; do
  case "$arg" in
    -y|--yes) YES=1 ;;
    --no-deps) DEPS=0 ;;
    --no-desktop) DESKTOP=0 ;;
    --no-autostart) AUTOSTART=0 ;;
    --uninstall) UNINSTALL=1 ;;
    --purge) PURGE=1 ;;
    -h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $arg (see --help)" >&2; exit 2 ;;
  esac
done

case "$OS" in
  Darwin) APP_HOME="$HOME/Library/Application Support/AndroidMirror" ;;
  Linux)  APP_HOME="${XDG_DATA_HOME:-$HOME/.local/share}/android-mirror" ;;
  *) echo "Unsupported OS: $OS (Linux and macOS only)." >&2; exit 1 ;;
esac
VENV="$APP_HOME/venv"
BIN_DIR="$HOME/.local/bin"
CLI="$VENV/bin/android-mirror"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
die()  { printf '\033[31mError:\033[0m %s\n' "$*" >&2; exit 1; }
ask()  { # ask "question" -> 0 for yes
  [ "$YES" = 1 ] && return 0
  [ -t 0 ] || return 0
  printf '%s [Y/n] ' "$1"; read -r reply
  case "$reply" in [nN]*) return 1 ;; *) return 0 ;; esac
}

# ------------------------------------------------------------------ uninstall
if [ "$UNINSTALL" = 1 ]; then
  if [ -x "$CLI" ]; then
    args=(uninstall --yes); [ "$PURGE" = 1 ] && args+=(--purge)
    exec "$CLI" "${args[@]}"
  fi
  echo "Android Mirror is not installed in $VENV."
  exit 0
fi

# ------------------------------------------------------------------ python
bold "Android Mirror installer ($OS)"

python_ok() { "$1" -c 'import sys, venv, ensurepip; sys.exit(sys.version_info < (3, 9))' >/dev/null 2>&1; }

find_python() {
  for p in python3.14 python3.13 python3.12 python3.11 python3.10 python3.9 python3 \
           /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
    if command -v "$p" >/dev/null 2>&1 && python_ok "$(command -v "$p")"; then command -v "$p"; return 0; fi
  done
  return 1
}

PY="$(find_python || true)"
if [ -z "$PY" ]; then
  echo "Python 3.9+ with the venv module is required."
  if [ "$OS" = Darwin ]; then
    if command -v brew >/dev/null 2>&1; then
      ask "Install Python with Homebrew (brew install python)?" && brew install python
    else
      info "Install the Xcode command line tools (includes Python 3):  xcode-select --install"
      info "or Homebrew from https://brew.sh and then:  brew install python"
    fi
  elif command -v apt-get >/dev/null 2>&1; then
    ask "Install python3-venv with apt (needs your password)?" && sudo apt-get install -y python3 python3-venv
  elif command -v dnf >/dev/null 2>&1; then
    ask "Install python3 with dnf (needs your password)?" && sudo dnf install -y python3
  elif command -v pacman >/dev/null 2>&1; then
    ask "Install python with pacman (needs your password)?" && sudo pacman -S --needed --noconfirm python
  elif command -v zypper >/dev/null 2>&1; then
    ask "Install python3 with zypper (needs your password)?" && sudo zypper --non-interactive install python3
  fi
  PY="$(find_python || true)"
  [ -n "$PY" ] || die "Python 3.9+ (with venv) not found. Install it and run this installer again."
fi
info "Python: $PY ($("$PY" -c 'import platform; print(platform.python_version())'))"

# ------------------------------------------------------------------ package source
WHEEL="$(ls -t "$HERE"/*.whl "$HERE"/dist/*.whl 2>/dev/null | head -n 1 || true)"
if [ -f "$HERE/pyproject.toml" ]; then
  SRC="$HERE"; info "Installing from source: $HERE"
elif [ -n "$WHEEL" ]; then
  SRC="$WHEEL"; info "Installing package: $(basename "$WHEEL")"
else
  die "Nothing to install: run this from the source checkout or the unpacked release bundle."
fi

# ------------------------------------------------------------------ virtualenv
mkdir -p "$APP_HOME"
if [ -x "$VENV/bin/python" ] && "$VENV/bin/python" -c 'import sys' >/dev/null 2>&1; then
  info "Upgrading existing installation in $VENV"
  "$CLI" stop >/dev/null 2>&1 || true
else
  rm -rf "$VENV"
  info "Creating virtual environment in $VENV"
  "$PY" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade pip
"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade "$SRC"
# Same version number but newer code (e.g. reinstalling from an edited checkout)? Replace the app itself anyway.
"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check --force-reinstall --no-deps "$SRC"
info "Installed $("$CLI" --version)"

mkdir -p "$BIN_DIR"
ln -sf "$CLI" "$BIN_DIR/android-mirror"
info "Command: $BIN_DIR/android-mirror"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) RC="$HOME/.profile"; [ "$OS" = Darwin ] && RC="$HOME/.zprofile"
     info "Note: $BIN_DIR is not on your PATH. Add it with:"
     info "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> $RC" ;;
esac

# ------------------------------------------------------------------ desktop integration
if [ "$DESKTOP" = 1 ]; then
  bold "Desktop integration"
  if [ "$AUTOSTART" = 1 ]; then "$CLI" desktop install; else "$CLI" desktop install --no-autostart; fi
fi

# ------------------------------------------------------------------ adb + scrcpy
if [ "$DEPS" = 1 ]; then
  bold "Checking adb and scrcpy"
  if "$CLI" doctor >/dev/null 2>&1; then
    "$CLI" doctor | sed -n '2,3p'
  elif [ "$YES" = 1 ] || [ ! -t 0 ]; then
    "$CLI" install-deps --yes || info "Some tools are still missing. Finish setup from the web UI (Setup & dependencies)."
  else
    "$CLI" install-deps || info "Some tools are still missing. Finish setup from the web UI (Setup & dependencies)."
  fi
fi

# ------------------------------------------------------------------ start
bold "Starting"
"$CLI" start || true
echo
bold "Done. Android Mirror is at http://localhost:8765"
if [ "$OS" = Darwin ]; then
  info "Open it any time from Applications › Android Mirror or with: android-mirror"
else
  info "Open it any time from your app menu (Android Mirror) or with: android-mirror"
fi
info "Uninstall: android-mirror uninstall   (add --purge to also delete remembered devices)"
if [ -t 1 ] && [ "$YES" = 0 ] && ask "Open it in your browser now?"; then "$CLI" open || true; fi
