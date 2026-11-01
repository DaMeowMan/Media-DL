#!/usr/bin/env python3
"""
media_dl.py - small Tk GUI around yt-dlp (videos) and gallery-dl (images).

* Re-launches itself with the venv python if it isn't already running in it.
* Parses `yt-dlp --list-extractors` (cached per yt-dlp version) so you can
  pick a single site extractor and restrict yt-dlp to them (--use-extractors).
"""
from __future__ import annotations

import json
import locale
import os
import queue
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

# --------------------------------------------------------------------------
# Config (Targeting user local share path)
# --------------------------------------------------------------------------
VENV = Path("/home/dietpi/.local/share/media-dl/.venv")
VENV_BIN = VENV / "bin"
VENV_PY = VENV_BIN / "python"
YTDLP = VENV_BIN / "yt-dlp"
GDL = VENV_BIN / "gallery-dl"

DEFAULT_OUTDIR = str(Path.home() / "Downloads" / "media-dl")
CACHE_DIR = Path.home() / ".cache" / "media-dl"
CFG_PATH = Path.home() / ".config" / "media-dl" / "config.json"

PREVIEW_FORMAT = "bv*[height<=720]+ba/b[height<=720]/b"
MPV_OPTS: dict = {}
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"

IMG_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
MEDIA_EXT = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".flv", ".ts",
             ".mp3", ".m4a", ".opus", ".ogg", ".wav", ".flac"}

PCT_RE = re.compile(r"(\d+(?:\.\d+)?)%")


def _reexec_in_venv() -> None:
    if os.environ.get("MEDIA_DL_REEXEC") or not VENV_PY.exists():
        return
    try:
        if Path(sys.prefix).resolve() == VENV.resolve():
            return
    except OSError:
        return
    os.environ["MEDIA_DL_REEXEC"] = "1"
    os.execv(str(VENV_PY), [str(VENV_PY), os.path.abspath(__file__), *sys.argv[1:]])


_reexec_in_venv()

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError:
    sys.exit("tkinter is missing: sudo apt install python3-tk")

try:
    from PIL import Image, ImageTk
    PIL_OK = True
except Exception:
    PIL_OK = False

try:
    import mpv  # python-mpv (needs libmpv)
    MPV_OK = True
except Exception:
    MPV_OK = False


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def child_env() -> dict:
    env = os.environ.copy()
    env["PATH"] = f"{VENV_BIN}:{env.get('PATH', '')}"
    env["PYTHONUNBUFFERED"] = "1"
    return env


BROKEN_SUFFIX = " (CURRENTLY BROKEN)"


def parse_extractors(text: str) -> list[dict]:
    seen, out = set(), []
    for raw in text.splitlines():
        if not raw.strip() or raw[0].isspace():
            continue
        line = raw.strip()
        broken = line.endswith(BROKEN_SUFFIX)
        if broken:
            line = line[: -len(BROKEN_SUFFIX)].rstrip()
        if line and line not in seen:
            seen.add(line)
            out.append({"name": line, "broken": broken})
    return out


def load_extractors(force: bool = False) -> list[dict]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / "extractors.json"
    
    ver = "unknown"
    if YTDLP.exists():
        try:
            ver = subprocess.run([str(YTDLP), "--version"], capture_output=True, text=True,
                                 timeout=30, env=child_env()).stdout.strip()
        except Exception:
            pass

    if not force and cache.exists():
        try:
            data = json.loads(cache.read_text())
            if data.get("extractors"):
                return data["extractors"]
        except Exception:
            pass

    exts = []
    if YTDLP.exists():
        try:
            res = subprocess.run([str(YTDLP), "--list-extractors"], capture_output=True, text=True,
                                 timeout=60, env=child_env())
            exts = parse_extractors(res.stdout)
        except Exception:
            pass

    if not exts:
        try:
            import yt_dlp.extractor as E
            for ie in E.gen_extractor_classes():
                name = getattr(ie, 'IE_NAME', None)
                if name and name != "generic":
                    exts.append({"name": name, "broken": getattr(ie, '_DEPRECATED', False)})
        except Exception:
            pass

    if exts:
        cache.write_text(json.dumps({"version": ver, "extractors": exts}))
    return exts


_ie_cache: dict = {}


def _ytdlp_classes():
    if "ies" not in _ie_cache:
        try:
            import yt_dlp.extractor as E
            _ie_cache["ies"] = [ie for ie in E.gen_extractor_classes() if ie.IE_NAME != "generic"]
        except Exception:
            _ie_cache["ies"] = []
    return _ie_cache["ies"]


def ytdlp_matches(url: str) -> list[str]:
    return [ie.IE_NAME for ie in _ytdlp_classes() if ie.suitable(url)]


def supported_by_ytdlp(url: str, allowed: str | None = None) -> bool:
    try:
        names = ytdlp_matches(url)
    except Exception:
        return False
    return any((allowed is None or n == allowed) for n in names)


def supported_by_gdl(url: str) -> bool:
    try:
        import gallery_dl.extractor as GE
        return GE.find(url) is not None
    except Exception:
        return False


def load_cfg() -> dict:
    try:
        return json.loads(CFG_PATH.read_text())
    except Exception:
        return {}


def save_cfg(cfg: dict) -> None:
    try:
        CFG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CFG_PATH.write_text(json.dumps(cfg, indent=2))
    except Exception:
        pass


def play_external(target: str, proxy: str = "") -> None:
    for name in ("mpv", "vlc", "ffplay"):
        exe = shutil.which(name)
        if not exe:
            continue
        if name == "mpv":
            script_opts = f"ytdl_hook-ytdl_path={YTDLP}"
            if proxy:
                script_opts += f",ytdl_hook-ytdl_proxy={proxy}"
            cmd = [exe, f"--script-opts={script_opts}",
                   f"--ytdl-format={PREVIEW_FORMAT}", target]
        else:
            cmd = [exe, target]
        try:
            subprocess.Popen(cmd, env=child_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except Exception as e:
            print(f"Failed to launch external player {name}: {e}")


# --------------------------------------------------------------------------
# Video player (embedded mpv)
# --------------------------------------------------------------------------
class Player:
    def __init__(self, frame: tk.Frame, log):
        self.frame = frame
        self.log = log
        self.mpv = None

    def _ensure(self, proxy: str = "") -> bool:
        if self.mpv is not None:
            return True
        if not MPV_OK:
            return False
        try:
            locale.setlocale(locale.LC_NUMERIC, "C")
            if self.frame:
                self.frame.update_idletasks()
            opts = {
                "wid": str(self.frame.winfo_id()) if self.frame else "",
                "ytdl": True,
                "script_opts": f"ytdl_hook-ytdl_path={YTDLP}",
                "ytdl_format": PREVIEW_FORMAT,
                "input_default_bindings": True,
                "input_vo_keyboard": True,
                "osc": True,
                **MPV_OPTS,
            }
            if proxy:
                opts["script_opts"] += f",ytdl_hook-ytdl_proxy={proxy}"
            self.mpv = mpv.MPV(**opts)
            return True
        except Exception as e:
            self.log(f"embedded mpv failed ({e})")
            self.mpv = None
            return False

    def play(self, target: str, proxy: str = "") -> None:
        if self._ensure(proxy):
            self.mpv.play(target)
            self.mpv.pause = False
            return
        play_external(target, proxy)

    def toggle_pause(self) -> None:
        if self.mpv is not None:
            self.mpv.pause = not self.mpv.pause

    def stop(self) -> None:
        try:
            if self.mpv is not None:
                self.mpv.stop()
        except Exception:
            pass

    def set_volume(self, v: float) -> None:
        if self.mpv is not None:
            try:
                self.mpv.volume = float(v)
            except Exception:
                pass

    def close(self) -> None:
        self.stop()
        try:
            if self.mpv is not None:
                self.mpv.terminate()
        except Exception:
            pass
        self.mpv = None


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("media-dl  (yt-dlp + gallery-dl)")
        self.geometry("1280x880")
        try:
            ttk.Style().theme_use("clam")
        except Exception:
            pass

        self.cfg = load_cfg()
        self.q: queue.Queue = queue.Queue()
        self.proc: subprocess.Popen | None = None
        self.cancelled = False
        self.busy = False

        self.ext_all: list[str] = []
        self.ext_broken: set[str] = set()
        self.ext_shown: list[str] = []
        self.selected: str | None = self.cfg.get("selected", None)

        self.files: list[str] = []
        self.tmpdirs: list[Path] = []
        self.img_ref = None

        # Instantiate player before UI so volume scaling works
        self.player = Player(None, self.log)

        self._build_ui()
        
        # Link video frame to player
        self.player.frame = self.video_frame

        self.scan_output_dir()

        if not YTDLP.exists():
            self.log(f"WARNING: {YTDLP} not found")
        if not GDL.exists():
            self.log(f"WARNING: {GDL} not found")
        if not PIL_OK:
            self.log("Pillow not installed: image previews limited to PNG/GIF (pip install pillow)")

        self.after(100, self._poll)
        self.load_extractors_async()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self) -> None:
        c = self.cfg
        self.url_var = tk.StringVar()
        self.mode_var = tk.StringVar(value=c.get("mode", "auto"))
        self.tie_var = tk.StringVar(value=c.get("tie", "video"))
        self.out_var = tk.StringVar(value=c.get("outdir", DEFAULT_OUTDIR))
        has_ffmpeg = bool(shutil.which("ffmpeg", path=child_env()["PATH"]))
        self.fmt_var = tk.StringVar(value=c.get("fmt", "bv*+ba/b" if has_ffmpeg else "b"))
        self.noplaylist_var = tk.BooleanVar(value=c.get("noplaylist", True))
        self.cookies_var = tk.StringVar(value=c.get("cookies", ""))
        self.proxy_var = tk.StringVar(value=c.get("proxy", ""))
        self.impersonate_var = tk.StringVar(value=c.get("impersonate", "chrome-151:macos"))
        self.ytx_var = tk.StringVar(value=c.get("ytx", ""))
        self.gdx_var = tk.StringVar(value=c.get("gdx", ""))
        self.restrict_var = tk.BooleanVar(value=c.get("restrict", False))
        self.generic_var = tk.BooleanVar(value=c.get("generic", False))
        self.hide_broken = tk.BooleanVar(value=True)
        self.filter_var = tk.StringVar()

        # Transcode options (off by default)
        self.transcode_var = tk.BooleanVar(value=c.get("transcode", False))
        self.transcode_fmt_var = tk.StringVar(value=c.get("transcode_fmt", "mp4"))

        # Image Convert options (off by default)
        self.img_conv_var = tk.BooleanVar(value=c.get("img_conv", False))
        self.img_conv_fmt_var = tk.StringVar(value=c.get("img_conv_fmt", "webp"))

        # --- URL row (Top Toolbar)
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        top.columnconfigure(0, weight=1)
        ent = ttk.Entry(top, textvariable=self.url_var, font=("TkDefaultFont", 11))
        ent.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ent.bind("<Return>", lambda e: self.on_download())
        ttk.Button(top, text="Paste", command=self.paste).grid(row=0, column=1, padx=2)
        self.btn_dl = ttk.Button(top, text="Download", command=self.on_download)
        self.btn_dl.grid(row=0, column=2, padx=2)
        self.btn_cancel = ttk.Button(top, text="Cancel", command=self.cancel, state="disabled")
        self.btn_cancel.grid(row=0, column=3, padx=2)

        # --- Main Workspace PanedWindow
        hp = ttk.PanedWindow(self, orient="horizontal")
        hp.pack(fill="both", expand=True, padx=8, pady=4)

        # Left pane: Extractors (single-select browse mode)
        left = ttk.LabelFrame(hp, text="yt-dlp extractor", padding=6)
        hp.add(left, weight=1)
        fr = ttk.Frame(left)
        fr.pack(fill="x", pady=(0, 4))
        ttk.Label(fr, text="Filter").pack(side="left")
        fe = ttk.Entry(fr, textvariable=self.filter_var)
        fe.pack(side="left", fill="x", expand=True, padx=4)
        self.filter_var.trace_add("write", lambda *a: self.refresh_ext_list())

        lf = ttk.Frame(left)
        lf.pack(fill="both", expand=True, pady=4)
        self.ext_lb = tk.Listbox(lf, selectmode="browse", exportselection=False, width=32)
        sb = ttk.Scrollbar(lf, orient="vertical", command=self.ext_lb.yview)
        self.ext_lb.config(yscrollcommand=sb.set)
        self.ext_lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.ext_lb.bind("<<ListboxSelect>>", self.on_ext_select)

        bf = ttk.Frame(left)
        bf.pack(fill="x", pady=4)
        ttk.Button(bf, text="Match URL", command=self.match_url).pack(side="left")
        ttk.Button(bf, text="Clear", command=self.clear_selected).pack(side="left", padx=4)
        ttk.Button(bf, text="Refresh", command=lambda: self.load_extractors_async(True)).pack(side="left")
        self.sel_label = ttk.Label(left, text="None selected")
        self.sel_label.pack(anchor="w", pady=2)
        ttk.Checkbutton(left, text="Restrict yt-dlp to selected", variable=self.restrict_var).pack(anchor="w")
        ttk.Checkbutton(left, text="…and also allow 'generic'", variable=self.generic_var).pack(anchor="w")
        ttk.Checkbutton(left, text="Hide broken extractors", variable=self.hide_broken,
                        command=self.refresh_ext_list).pack(anchor="w")

        # Right pane (Notebook for Preview, Advanced, Log) + Files at bottom
        right = ttk.PanedWindow(hp, orient="vertical")
        hp.add(right, weight=4)

        self.nb = ttk.Notebook(right)
        right.add(self.nb, weight=5)

        # 1. Preview Tab
        prev_tab = ttk.Frame(self.nb)
        self.nb.add(prev_tab, text="Preview")
        self.stage = ttk.Frame(prev_tab)
        self.stage.pack(fill="both", expand=True, padx=4, pady=4)
        self.img_label = tk.Label(self.stage, bg="#111", fg="#aaa", text="Select a downloaded file below to preview/play")
        self.video_frame = tk.Frame(self.stage, bg="black")
        self.show_stage("image")
        
        ctl = ttk.Frame(prev_tab, padding=4)
        ctl.pack(fill="x")
        ttk.Button(ctl, text="Play/Pause", command=lambda: self.player.toggle_pause()).pack(side="left", padx=2)
        ttk.Button(ctl, text="Stop", command=lambda: self.player.stop()).pack(side="left", padx=2)
        ttk.Button(ctl, text="Open External Player", command=self.open_external_current).pack(side="left", padx=10)
        ttk.Label(ctl, text="Volume:").pack(side="left", padx=(16, 4))
        vol = ttk.Scale(ctl, from_=0, to=100, orient="horizontal", length=140,
                        command=lambda v: self.player.set_volume(v))
        vol.set(80)
        vol.pack(side="left")

        # 2. Advanced Tab
        adv_tab = ttk.Frame(self.nb, padding=12)
        self.nb.add(adv_tab, text="Advanced")
        
        adv_frame = ttk.LabelFrame(adv_tab, text="Configuration & Advanced Settings", padding=12)
        adv_frame.pack(fill="both", expand=True)
        adv_frame.columnconfigure(1, weight=1)

        ttk.Label(adv_frame, text="Mode:").grid(row=0, column=0, sticky="w", pady=6)
        mf = ttk.Frame(adv_frame)
        mf.grid(row=0, column=1, sticky="w", pady=6)
        for text, val in (("Auto", "auto"), ("Video (yt-dlp)", "video"), ("Image (gallery-dl)", "image")):
            ttk.Radiobutton(mf, text=text, value=val, variable=self.mode_var).pack(side="left", padx=6)
        
        ttk.Label(adv_frame, text="If both match:").grid(row=0, column=2, sticky="e", padx=(16, 4))
        ttk.Combobox(adv_frame, textvariable=self.tie_var, values=("video", "image", "both"),
                     width=8, state="readonly").grid(row=0, column=3, sticky="w")

        ttk.Label(adv_frame, text="Output Directory:").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Entry(adv_frame, textvariable=self.out_var).grid(row=1, column=1, columnspan=2, sticky="ew", pady=6)
        ttk.Button(adv_frame, text="Browse…", command=self.browse_out).grid(row=1, column=3, padx=4, pady=6)

        ttk.Label(adv_frame, text="yt-dlp Format:").grid(row=2, column=0, sticky="w", pady=6)
        ttk.Combobox(adv_frame, textvariable=self.fmt_var, width=38,
                     values=("bv*+ba/b", "b", "ba/b (Audio Only)", "bv*[height<=720]+ba/b[height<=720]",
                             "bv*[height<=480]+ba/b[height<=480]")
                     ).grid(row=2, column=1, sticky="w", pady=6)
        ttk.Checkbutton(adv_frame, text="Single video only (--no-playlist)",
                        variable=self.noplaylist_var).grid(row=2, column=2, columnspan=2, sticky="w", padx=12)

        # Transcode settings row
        ttk.Checkbutton(adv_frame, text="Transcode Media", variable=self.transcode_var).grid(row=3, column=0, sticky="w", pady=6)
        tf_frame = ttk.Frame(adv_frame)
        tf_frame.grid(row=3, column=1, sticky="w", pady=6)
        ttk.Label(tf_frame, text="Format:").pack(side="left", padx=(0, 4))
        ttk.Combobox(tf_frame, textvariable=self.transcode_fmt_var, width=12, state="readonly",
                     values=("mp4", "mkv", "webm", "avi", "mov", "mp3", "m4a", "opus", "flac")).pack(side="left")

        # Image Convert settings row
        ttk.Checkbutton(adv_frame, text="Convert Images", variable=self.img_conv_var).grid(row=4, column=0, sticky="w", pady=6)
        ic_frame = ttk.Frame(adv_frame)
        ic_frame.grid(row=4, column=1, sticky="w", pady=6)
        ttk.Label(ic_frame, text="Format:").pack(side="left", padx=(0, 4))
        ttk.Combobox(ic_frame, textvariable=self.img_conv_fmt_var, width=12, state="readonly",
                     values=("webp", "png", "jpg", "jpeg")).pack(side="left")

        ttk.Label(adv_frame, text="Cookies File:").grid(row=5, column=0, sticky="w", pady=6)
        ttk.Entry(adv_frame, textvariable=self.cookies_var).grid(row=5, column=1, columnspan=2, sticky="ew", pady=6)
        ttk.Button(adv_frame, text="Browse…", command=self.browse_cookies).grid(row=5, column=3, padx=4, pady=6)

        ttk.Label(adv_frame, text="Proxy URL:").grid(row=6, column=0, sticky="w", pady=6)
        ttk.Entry(adv_frame, textvariable=self.proxy_var).grid(row=6, column=1, columnspan=3, sticky="ew", pady=6)

        ttk.Label(adv_frame, text="Impersonate Target:").grid(row=7, column=0, sticky="w", pady=6)
        ttk.Entry(adv_frame, textvariable=self.impersonate_var).grid(row=7, column=1, columnspan=3, sticky="ew", pady=6)

        ttk.Label(adv_frame, text="Extra yt-dlp Args:").grid(row=8, column=0, sticky="w", pady=6)
        ttk.Entry(adv_frame, textvariable=self.ytx_var).grid(row=8, column=1, columnspan=3, sticky="ew", pady=6)

        ttk.Label(adv_frame, text="Extra gallery-dl Args:").grid(row=9, column=0, sticky="w", pady=6)
        ttk.Entry(adv_frame, textvariable=self.gdx_var).grid(row=9, column=1, columnspan=3, sticky="ew", pady=6)

        # 3. Log Tab
        lg_tab = ttk.Frame(self.nb)
        self.nb.add(lg_tab, text="Log")
        self.log_text = tk.Text(lg_tab, wrap="none", state="disabled")
        lsb = ttk.Scrollbar(lg_tab, orient="vertical", command=self.log_text.yview)
        self.log_text.config(yscrollcommand=lsb.set)
        lsb.pack(side="right", fill="y")
        self.log_text.pack(fill="both", expand=True)

        # Bottom Pane: Downloaded files list & progress
        bot = ttk.LabelFrame(right, text="Downloaded files (click to play/preview)", padding=6)
        right.add(bot, weight=1)
        ff = ttk.Frame(bot)
        ff.pack(fill="both", expand=True)
        self.files_lb = tk.Listbox(ff, height=5, exportselection=False)
        fsb = ttk.Scrollbar(ff, orient="vertical", command=self.files_lb.yview)
        self.files_lb.config(yscrollcommand=fsb.set)
        self.files_lb.pack(side="left", fill="both", expand=True)
        fsb.pack(side="right", fill="y")
        self.files_lb.bind("<<ListboxSelect>>", self.on_file_select)
        
        self.progress = ttk.Progressbar(bot, maximum=100)
        self.progress.pack(fill="x", pady=(6, 2))
        self.status = ttk.Label(bot, text="Ready")
        self.status.pack(anchor="w")

    def show_stage(self, which: str) -> None:
        self.img_label.pack_forget()
        self.video_frame.pack_forget()
        if which == "video":
            self.video_frame.pack(fill="both", expand=True)
        else:
            self.img_label.pack(fill="both", expand=True)

    def log(self, text: str) -> None:
        def _do():
            self.log_text.config(state="normal")
            self.log_text.insert("end", text + "\n")
            self.log_text.see("end")
            self.log_text.config(state="disabled")
        if threading.current_thread() is threading.main_thread():
            _do()
        else:
            self.q.put(("log", text))

    def paste(self) -> None:
        try:
            self.url_var.set(self.clipboard_get().strip())
        except tk.TclError:
            pass

    def browse_out(self) -> None:
        d = filedialog.askdirectory(initialdir=self.out_var.get() or str(Path.home()))
        if d:
            self.out_var.set(d)
            self.scan_output_dir()

    def browse_cookies(self) -> None:
        f = filedialog.askopenfilename(initialdir=str(Path.home()))
        if f:
            self.cookies_var.set(f)

    def scan_output_dir(self) -> None:
        outdir = Path(self.out_var.get().strip() or DEFAULT_OUTDIR)
        if not outdir.exists():
            return
        
        self.files.clear()
        self.files_lb.delete(0, "end")
        
        found = []
        for root, _, filenames in os.walk(outdir):
            for filename in filenames:
                ext = Path(filename).suffix.lower()
                if ext in IMG_EXT | MEDIA_EXT:
                    found.append(os.path.join(root, filename))
        
        found.sort(key=lambda p: os.path.getmtime(p), reverse=True)

        for path in found:
            self.files.append(path)
            self.files_lb.insert("end", os.path.basename(path))

        if found:
            self.status.config(text=f"Loaded {len(found)} existing file(s) from directory")

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.btn_dl.config(state="disabled" if busy else "normal")
        self.btn_cancel.config(state="normal" if busy else "disabled")

    def snapshot(self) -> dict:
        return {
            "mode": self.mode_var.get(), "tie": self.tie_var.get(),
            "outdir": self.out_var.get().strip() or DEFAULT_OUTDIR,
            "fmt": self.fmt_var.get().strip() or "b",
            "noplaylist": self.noplaylist_var.get(),
            "cookies": self.cookies_var.get().strip(),
            "proxy": self.proxy_var.get().strip(),
            "impersonate": self.impersonate_var.get().strip(),
            "ytx": self.ytx_var.get().strip(), "gdx": self.gdx_var.get().strip(),
            "restrict": self.restrict_var.get(), "generic": self.generic_var.get(),
            "selected": self.selected,
            "transcode": self.transcode_var.get(),
            "transcode_fmt": self.transcode_fmt_var.get(),
            "img_conv": self.img_conv_var.get(),
            "img_conv_fmt": self.img_conv_fmt_var.get(),
        }

    def load_extractors_async(self, force: bool = False) -> None:
        self.status.config(text="Loading yt-dlp extractor list…")

        def work():
            try:
                self.q.put(("extractors", load_extractors(force)))
            except Exception as e:
                self.q.put(("log", f"extractor list failed: {e}"))
        threading.Thread(target=work, daemon=True).start()

    def refresh_ext_list(self) -> None:
        f = self.filter_var.get().lower().strip()
        hide = self.hide_broken.get()
        self.ext_shown = [n for n in self.ext_all
                          if f in n.lower() and not (hide and n in self.ext_broken)]
        self.ext_lb.delete(0, "end")
        for i, n in enumerate(self.ext_shown):
            self.ext_lb.insert("end", n + ("  [broken]" if n in self.ext_broken else ""))
            if self.selected and n == self.selected:
                self.ext_lb.selection_set(i)
                self.ext_lb.see(i)
        label_txt = f"Selected: {self.selected}" if self.selected else "None selected"
        self.sel_label.config(text=f"{label_txt}  ({len(self.ext_shown)} shown)")

    def on_ext_select(self, _evt=None) -> None:
        sel = self.ext_lb.curselection()
        if sel:
            idx = sel[0]
            self.selected = self.ext_shown[idx]
        else:
            self.selected = None
        label_txt = f"Selected: {self.selected}" if self.selected else "None selected"
        self.sel_label.config(text=f"{label_txt}  ({len(self.ext_shown)} shown)")

    def clear_selected(self) -> None:
        self.selected = None
        self.ext_lb.selection_clear(0, "end")
        self.refresh_ext_list()

    def match_url(self) -> None:
        url = self.url_var.get().strip()
        if not url:
            return
        self.status.config(text="Matching URL against yt-dlp extractors…")

        def work():
            try:
                matches = ytdlp_matches(url)
                self.q.put(("match", matches[0] if matches else None))
            except Exception as e:
                self.q.put(("log", f"match failed: {e}"))
        threading.Thread(target=work, daemon=True).start()

    def _allowed(self, cfg: dict) -> str | None:
        if cfg["restrict"] and cfg["selected"]:
            return cfg["selected"]
        return None

    def _decide(self, url: str, cfg: dict) -> list[str]:
        if cfg["mode"] == "video":
            return ["video"]
        if cfg["mode"] == "image":
            return ["image"]
        v = supported_by_ytdlp(url, self._allowed(cfg))
        g = supported_by_gdl(url)
        self.q.put(("log", f"auto-detect: yt-dlp={'yes' if v else 'no'}  gallery-dl={'yes' if g else 'no'}"))
        if v and g:
            return ["video", "image"] if cfg["tie"] == "both" else [cfg["tie"]]
        if v:
            return ["video"]
        if g:
            return ["image"]
        self.q.put(("log", "no dedicated extractor found, trying yt-dlp"))
        return ["video"]

    def build_cmd(self, kind: str, url: str, cfg: dict) -> list[str]:
        outdir = cfg["outdir"]
        proxy = cfg["proxy"]
        impersonate = cfg["impersonate"]
        if kind == "video":
            fmt = cfg["fmt"]
            if "Audio Only" in fmt:
                fmt = "ba/b"
            cmd = [str(YTDLP) if YTDLP.exists() else "yt-dlp", "--ignore-config", "--newline", "--progress", "--no-simulate",
                   "--embed-metadata", "--embed-thumbnail",
                   "--print", "after_move:filepath",
                   "-P", outdir, "-o", "%(extractor)s/%(title).150B [%(id)s].%(ext)s",
                   "-f", fmt]
            if impersonate:
                cmd += ["--impersonate", impersonate]
            if cfg["transcode"]:
                tf = cfg["transcode_fmt"]
                if tf in ("mp3", "m4a", "opus", "flac"):
                    cmd += ["--extract-audio", "--audio-format", tf]
                else:
                    cmd += ["--recode-video", tf]
            if proxy:
                cmd += ["--proxy", proxy]
            if cfg["noplaylist"]:
                cmd.append("--no-playlist")
            if cfg["restrict"] and cfg["selected"]:
                names = [re.escape(cfg["selected"].lower())]
                if cfg["generic"]:
                    names.append("generic")
                cmd += ["--use-extractors", ",".join(names)]
            if cfg["cookies"]:
                cmd += ["--cookies", cfg["cookies"]]
            if cfg["ytx"]:
                cmd += shlex.split(cfg["ytx"])
        else:
            cmd = [str(GDL) if GDL.exists() else "gallery-dl", "-d", outdir]
            if cfg["img_conv"]:
                cmd += ["--postprocessor", f"convert={cfg['img_conv_fmt']}"]
            if proxy:
                cmd += ["--proxy", proxy]
            if cfg["cookies"]:
                cmd += ["--cookies", cfg["cookies"]]
            if cfg["gdx"]:
                cmd += shlex.split(cfg["gdx"])
        cmd.append(url)
        return cmd

    def on_download(self) -> None:
        url = self.url_var.get().strip()
        if not url or self.busy:
            return
        cfg = self.snapshot()
        Path(cfg["outdir"]).mkdir(parents=True, exist_ok=True)
        self.set_busy(True)
        self.progress["value"] = 0
        self.status.config(text="Working…")
        threading.Thread(target=self._download_worker, args=(url, cfg), daemon=True).start()

    def _download_worker(self, url: str, cfg: dict) -> None:
        self.cancelled = False
        try:
            for kind in self._decide(url, cfg):
                if self.cancelled:
                    break
                cmd = self.build_cmd(kind, url, cfg)
                self.q.put(("log", "$ " + shlex.join(cmd)))
                self.q.put(("status", f"{'yt-dlp' if kind == 'video' else 'gallery-dl'} running…"))
                rc = self.run_proc(cmd)
                self.q.put(("log", f"[exit code {rc}]"))
        except Exception as e:
            self.q.put(("log", f"error: {e}"))
        finally:
            self.q.put(("done", None))

    def run_proc(self, cmd: list[str]) -> int:
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, errors="replace", bufsize=1, env=child_env())
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.rstrip("\n")
            self.q.put(("log", line))
            if "[download]" in line:
                m = PCT_RE.search(line)
                if m:
                    self.q.put(("progress", float(m.group(1))))
            p = line[2:] if line.startswith("# ") else line
            if p.startswith("/") and os.path.isfile(p):
                self.q.put(("file", p))
        return self.proc.wait()

    def cancel(self) -> None:
        self.cancelled = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def open_external_current(self) -> None:
        self.player.stop()

        sel = self.files_lb.curselection()
        if sel:
            target = self.files[sel[0]]
        else:
            target = self.url_var.get().strip()
        
        if not target:
            return
        cfg = self.snapshot()
        play_external(target, cfg["proxy"])

    def show_file(self, path: str) -> None:
        ext = Path(path).suffix.lower()
        if ext in IMG_EXT:
            self.player.stop()
            self.show_stage("image")
            self.show_image(path)
        elif ext in MEDIA_EXT:
            self.show_stage("video")
            self.update_idletasks()
            self.player.play(path, self.proxy_var.get().strip())
        else:
            self.log(f"no preview for {path}")

    def show_image(self, path: str) -> None:
        self.update_idletasks()
        try:
            if PIL_OK:
                im = Image.open(path)
                w = max(self.stage.winfo_width() - 10, 300)
                h = max(self.stage.winfo_height() - 10, 300)
                try:
                    im.draft("RGB", (w, h))
                except Exception:
                    pass
                im.thumbnail((w, h))
                self.img_ref = ImageTk.PhotoImage(im)
            else:
                self.img_ref = tk.PhotoImage(file=path)
            self.img_label.config(image=self.img_ref, text="")
        except Exception as e:
            self.img_ref = None
            self.img_label.config(image="", text=f"Can't preview {Path(path).name}\n{e}")

    def _poll(self) -> None:
        try:
            while True:
                kind, val = self.q.get_nowait()
                try:
                    getattr(self, "_h_" + kind)(val)
                except Exception as e:
                    self.log(f"UI handler {kind} failed: {e}")
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _h_log(self, text):
        self.log(text)

    def _h_status(self, text):
        self.status.config(text=text)

    def _h_progress(self, pct):
        self.progress["value"] = pct

    def _h_file(self, path):
        if path in self.files:
            return
        self.files.insert(0, path)
        self.files_lb.insert(0, os.path.basename(path))
        self.files_lb.selection_clear(0, "end")
        self.files_lb.selection_set(0)
        self.show_file(path)

    def _h_done(self, _):
        self.set_busy(False)
        self.progress["value"] = 100 if not self.cancelled else 0
        self.status.config(text="Cancelled" if self.cancelled else f"Done ({len(self.files)} file(s) listed)")
        self.scan_output_dir()

    def _h_extractors(self, exts):
        self.ext_all = [e["name"] for e in exts]
        self.ext_broken = {e["name"] for e in exts if e["broken"]}
        self.refresh_ext_list()
        self.status.config(text=f"{len(self.ext_all)} yt-dlp extractors loaded")

    def _h_match(self, name):
        if not name:
            self.status.config(text="No dedicated yt-dlp extractor matches this URL")
            return
        self.selected = name
        self.filter_var.set("")
        self.refresh_ext_list()
        self.status.config(text=f"Matched extractor: {name}")

    def on_file_select(self, _evt=None) -> None:
        sel = self.files_lb.curselection()
        if sel:
            self.show_file(self.files[sel[0]])

    def on_close(self) -> None:
        save_cfg({
            "mode": self.mode_var.get(), "tie": self.tie_var.get(),
            "outdir": self.out_var.get(), "fmt": self.fmt_var.get(),
            "noplaylist": self.noplaylist_var.get(), "cookies": self.cookies_var.get(),
            "proxy": self.proxy_var.get(),
            "impersonate": self.impersonate_var.get(),
            "ytx": self.ytx_var.get(), "gdx": self.gdx_var.get(),
            "restrict": self.restrict_var.get(), "generic": self.generic_var.get(),
            "selected": self.selected,
            "transcode": self.transcode_var.get(),
            "transcode_fmt": self.transcode_fmt_var.get(),
            "img_conv": self.img_conv_var.get(),
            "img_conv_fmt": self.img_conv_fmt_var.get(),
        })
        self.cancel()
        self.player.close()
        for d in self.tmpdirs:
            shutil.rmtree(d, ignore_errors=True)
        self.destroy()


if __name__ == "__main__":
    try:
        App().mainloop()
    except KeyboardInterrupt:
        print("\nExiting cleanly...")
        try:
            sys.exit(0)
        except SystemExit:
            os._exit(0)
