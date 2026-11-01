#!/usr/bin/env bash
# install.sh - install media-dl on any Linux distro, system-wide or per-user.
#
#   ./generic/install.sh [install|uninstall] [--user|--system] [--prefix DIR]
#                        [--no-setup] [--force]
#
#   --system   (default as root)  files under PREFIX (default /usr/local),
#              venv in /opt/media-dl/venv, yt-dlp config in /etc/yt-dlp.conf
#   --user     (default otherwise) files under ~/.local, venv in
#              ~/.local/share/media-dl/venv, yt-dlp config in ~/.config/yt-dlp/config
#   --no-setup skip building the venv (copy files only)
#   --force    install even if system dependencies look missing
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACTION=install MODE="" PREFIX="" DO_SETUP=1 FORCE=0

usage() { sed -n '2,13p' "${BASH_SOURCE[0]}"; }
while [ $# -gt 0 ]; do
    case "$1" in
        install|uninstall) ACTION="$1" ;;
        --user) MODE=user ;;
        --system) MODE=system ;;
        --prefix) PREFIX="${2:?--prefix needs a directory}"; shift ;;
        --no-setup) DO_SETUP=0 ;;
        --force) FORCE=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown option: $1" >&2; usage; exit 1 ;;
    esac
    shift
done

if [ -z "$MODE" ]; then
    if [ "$(id -u)" = 0 ]; then MODE=system; else MODE=user; fi
fi
if [ "$MODE" = system ]; then
    [ "$(id -u)" = 0 ] || { echo "--system needs root (use sudo)" >&2; exit 1; }
    PREFIX="${PREFIX:-/usr/local}"
    APP=/opt/media-dl
    CONF=/etc/yt-dlp.conf
else
    PREFIX="${PREFIX:-$HOME/.local}"
    APP="$HOME/.local/share/media-dl"
    CONF="$HOME/.config/yt-dlp/config"
fi
VENV="$APP/venv"
LIBDIR="$PREFIX/lib/media-dl"
BINDIR="$PREFIX/bin"
SHAREDIR="$PREFIX/share"

export MEDIA_DL_APP="$APP" MEDIA_DL_VENV="$VENV" MEDIA_DL_YTDLP_CONF="$CONF"

refresh_caches() {
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q "$SHAREDIR/applications" 2>/dev/null || true
    command -v gtk-update-icon-cache  >/dev/null 2>&1 && gtk-update-icon-cache -q -t -f "$SHAREDIR/icons/hicolor" 2>/dev/null || true
}

render() {
    sed -e "s|@LIBDIR@|$LIBDIR|g" -e "s|@BINDIR@|$BINDIR|g" -e "s|@VENV@|$VENV|g" "$1" > "$2"
}

hint() {
    local id like
    id="$(. /etc/os-release 2>/dev/null && echo "${ID:-}")"
    like="$(. /etc/os-release 2>/dev/null && echo "${ID_LIKE:-}")"
    echo
    echo "Install the missing system packages for your distro, then re-run:"
    case " $id $like " in
    *" debian "*|*" ubuntu "*|*" raspbian "*)
        echo "  sudo apt install python3 python3-tk python3-venv python3-pip python3-pil python3-pil.imagetk \\"
        echo "       curl ca-certificates ffmpeg atomicparsley aria2 mpv libmpv2" ;;
    *" fedora "*|*" rhel "*|*" centos "*)
        echo "  sudo dnf install python3 python3-tkinter python3-pip python3-pillow python3-pillow-tk \\"
        echo "       curl ffmpeg-free AtomicParsley aria2 mpv mpv-libs"
        echo "  (RHEL/Rocky/Alma: enable EPEL + RPM Fusion first; RPM Fusion's full 'ffmpeg' also works)" ;;
    *" suse "*|*" opensuse"*|*" sles "*)
        echo "  sudo zypper install python3 python3-tk python3-Pillow python3-Pillow-tk python3-pip \\"
        echo "       curl ffmpeg AtomicParsley aria2 mpv libmpv2   (ffmpeg/AtomicParsley may need the Packman repo)" ;;
    *" arch "*)
        echo "  sudo pacman -S python tk python-pillow python-pip uv curl ffmpeg atomicparsley aria2 mpv" ;;
    *" alpine "*)
        echo "  sudo apk add python3 py3-tkinter py3-pillow py3-pip uv curl ffmpeg atomicparsley aria2 mpv mpv-libs" ;;
    *)
        echo "  need: python3 (with tkinter, venv, pip), Pillow with ImageTk, curl, ffmpeg+ffprobe,"
        echo "        AtomicParsley, aria2, mpv + libmpv" ;;
    esac
}

check_deps() {
    local missing=()
    for c in python3 curl ffmpeg ffprobe aria2c mpv; do
        command -v "$c" >/dev/null 2>&1 || missing+=("$c")
    done
    command -v AtomicParsley >/dev/null 2>&1 || command -v atomicparsley >/dev/null 2>&1 || missing+=("AtomicParsley")
    if command -v python3 >/dev/null 2>&1; then
        python3 -c "import tkinter"        2>/dev/null || missing+=("python3-tkinter")
        python3 -c "import PIL.ImageTk"    2>/dev/null || missing+=("Pillow-ImageTk")
        python3 -c "import venv, ensurepip" 2>/dev/null || missing+=("python3-venv/pip")
    fi
    if command -v ldconfig >/dev/null 2>&1 && ! ldconfig -p 2>/dev/null | grep -q 'libmpv\.so'; then
        missing+=("libmpv")
    fi
    if [ ${#missing[@]} -gt 0 ]; then
        echo "Missing: ${missing[*]}" >&2
        hint >&2
        [ "$FORCE" = 1 ] || exit 1
        echo "--force given, continuing anyway" >&2
    fi
}

do_install() {
    check_deps
    echo "Installing media-dl ($MODE) -> $PREFIX, venv -> $VENV"
    install -d "$LIBDIR" "$BINDIR" "$SHAREDIR/applications" "$SHAREDIR/icons"

    sed -E 's|^VENV = Path\(.*\)$|VENV = Path(os.environ.get("MEDIA_DL_VENV", "'"$VENV"'"))|' \
        "$HERE/src/media_dl.py" > "$LIBDIR/media_dl.py"
    grep -q "$VENV" "$LIBDIR/media_dl.py" || { echo "could not patch VENV line in src/media_dl.py" >&2; exit 1; }
    chmod 755 "$LIBDIR/media_dl.py"

    install -m755 "$HERE/common/media-dl-setup" "$LIBDIR/media-dl-setup"
    render "$HERE/common/media-dl"         "$BINDIR/media-dl";        chmod 755 "$BINDIR/media-dl"
    render "$HERE/common/media-dl-update"  "$BINDIR/media-dl-update"; chmod 755 "$BINDIR/media-dl-update"
    render "$HERE/common/media-dl.desktop" "$SHAREDIR/applications/media-dl.desktop"
    cp -a "$HERE/assets/icons/hicolor" "$SHAREDIR/icons/"
    refresh_caches

    if [ "$DO_SETUP" = 1 ]; then
        "$LIBDIR/media-dl-setup"
    else
        echo "skipped venv build; run later: MEDIA_DL_APP=$APP $LIBDIR/media-dl-setup"
    fi

    echo
    echo "Done. Start it from the menu (Sound & Video -> Media-DL) or run: $BINDIR/media-dl"
    case ":$PATH:" in *":$BINDIR:"*) ;; *) echo "Note: $BINDIR is not on your PATH." ;; esac
}

do_uninstall() {
    echo "Removing media-dl ($MODE) from $PREFIX"
    [ -x "$LIBDIR/media-dl-setup" ] && "$LIBDIR/media-dl-setup" --purge || true
    rm -f  "$BINDIR/media-dl" "$BINDIR/media-dl-update" "$SHAREDIR/applications/media-dl.desktop"
    rm -f  "$SHAREDIR"/icons/hicolor/*/apps/media-dl.png
    rm -rf "$LIBDIR"
    refresh_caches
    echo "Done. (Settings in ~/.config/media-dl and ~/.cache/media-dl were left alone.)"
}

"do_$ACTION"
