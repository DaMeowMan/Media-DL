# Media-DL

A single-file Python/Tk desktop application for downloading videos via **yt-dlp** and images via **gallery-dl**, featuring embedded previews, extractor management, and automated virtual environment setup.

---

## ✨ Key Features

* **Smart URL Auto-Detection:** Automatically tests pasted URLs against both `yt-dlp` and `gallery-dl` engine lists, allowing seamless switching or downloading both formats simultaneously.
* **Embedded Media Previews:**
  * **Videos:** Stream live video previews in an embedded player (`python-mpv`).
  * **Images:** View 12-image thumbnail grids fetched directly via `gallery-dl`.
  * **Downloaded Files:** Click any downloaded item in the app's file list to preview it instantly.
* **Interactive Extractor Picker:** Dynamically search, filter, and inspect `yt-dlp` extractors. Click **Match URL** to auto-select matching engines and apply custom `--use-extractors` restrictions.
* **Automated Isolated Venv:** Automatically sets up and manages an isolated virtual environment (`uv`-accelerated) during installation without touching your system's global Python environment.
* **Custom Presets & Configs:** Easily customize download locations (default `~/Downloads/media-dl`), format selection, cookie files, and custom arguments saved in `~/.config/media-dl/config.json`.

---

## 📦 Installation

### Option 1: Debian / Ubuntu / Linux Mint / DietPi (`.deb`)
Download `media-dl_1.3.0_all.deb` from the Releases tab and run:

```bash
sudo apt install ./media-dl_1.3.0_all.deb
```

### Option 2: Generic Linux (Fedora, Arch, openSUSE, Alpine, etc.)
Download `media-dl-1.3.0.tar.gz` from the Releases tab, extract it, and run the included installer:

```bash
tar -xzf media-dl-1.3.0.tar.gz
cd media-dl-1.3.0

# System-wide installation (requires sudo):
sudo ./generic/install.sh

# Per-user installation (installs to ~/.local):
./generic/install.sh --user
```

### Option 3: Arch Linux (Build via PKGBUILD)
```bash
cd arch
makepkg -si
```

---

## 🛠️ Building Packages from Source

To compile Debian, RPM, or generic release packages locally from source code, run the build script:

```bash
./build.sh all 1.3.0
```

Compiled packages will be generated inside the local `dist/` directory.

---

## 🔄 Updates & Maintenance

To update internal engine dependencies (`yt-dlp`, `gallery-dl`, `python-mpv`, `mutagen`) at any time:

* **System-wide installations:** `sudo media-dl-update`
* **Per-user installations:** `media-dl-update`

To clean or rebuild the virtual environment:
* **System-wide installations:** `sudo /usr/lib/media-dl/media-dl-setup --purge`

---

## ⚠️ System Requirements & Notes

* **Display Server:** Requires a graphical session (X11 or XWayland; pure Wayland is untested).
* **Network:** Active internet connection is required at install time to download Python dependencies.
* **Impersonation Support:** `curl_cffi` is required for impersonation targets (supported on standard `x86_64` and `aarch64` Linux systems).