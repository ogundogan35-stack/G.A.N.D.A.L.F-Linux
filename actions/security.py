"""Security / anti-virus actions for Gandalf (Windows + Linux).

Gandalf can scan the machine for threats and remove them, but removal ALWAYS
requires explicit user confirmation and ALWAYS goes to the Trash / Recycle Bin
(never permanently deleted). System-critical folders are always protected and
can never be removed even with confirmation.

Two detection engines are combined:
  1. Signature engine — Windows Defender (MpCmdRun.exe) on Windows,
     ClamAV (clamscan) on Linux if installed.
  2. A built-in heuristic scanner — suspicious file types, known-bad names,
     and high-risk locations (Downloads / Temp / caches executables).

Supported actions (parameters.action):
    scan[:<path>]      scan a folder (default: the user's common download/temp
                       folders + Downloads). Reports a suspicious-file list.
    clean[:yes]        quarantine (to Trash/Recycle Bin) the previously reported
                       suspicious files. Requires parameters.confirm we got a
                       "yes" from the user; anything else refuses.
    status             signature-engine status (Defender / ClamAV).
    stop               cancel an in-progress scan.
    whitelist:<path>   remember a path/folder to skip in future scans.
"""

import os
import re
import time
import subprocess
import threading
import shutil
from pathlib import Path

from tts import edge_speak

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Suspicious / executable file extensions that a "normal" user download area
# should generally not contain. Treated as *suspicious* (never auto-remove);
# they are only removed after the user confirms.
_SUSPICIOUS_EXT = {
    ".exe", ".dll", ".scr", ".bat", ".cmd", ".com", ".ps1", ".vbs", ".vbe",
    ".js", ".jse", ".wsf", ".wsh", ".msi", ".msp", ".hta", ".reg", ".pif",
    ".cpl", ".lnk", ".iso", ".jar", ".apk", ".elf", ".bin",
}

# Extensions that are almost never legitimate when found in Downloads / Temp /
# caches immediately after being downloaded (script/preview executables
# commonly used as camouflage for malware). Still confirmation-gated.
_HIGH_RISK_EXT = {
    ".exe", ".scr", ".bat", ".cmd", ".ps1", ".vbs", ".hta", ".js", ".com",
    ".elf",
}

# Well-known malware-family file names (exact, case-insensitive). These bump a
# file to "high risk" but NEVER auto-remove — always user-confirmed.
_KNOWN_BAD_NAMES = {
    "runtimebroker.exe", "svchost.exe.tmp", "winupdate.exe",
    "microsoft-edge-helper.exe", "update.exe", "install_flash_player.exe",
    "licensechecker.exe", "bitcoinminer.exe", "miner.exe", "kmsauto.exe",
    "kk.exe", "wcry.exe", "locker.exe", "petya.exe",
}

# Suspicious names that suggest a malicious process / utility.
_SUSPICIOUS_NAME_TOKENS = [
    "crack", "keygen", "activator", "miner", "bitcoin", "monero",
    "kms", "rat", "stealer", "spyware", "trojan", "backdoor", "rootkit",
    "injector", "cheat", "hack",
]

# Roots that are NEVER allowed to be scanned/removed. Even if a scan somehow
# reports a file inside these, removal is refused.
if os.name == "nt":
    _PROTECTED_ROOTS = [
        os.environ.get("WINDIR") or r"C:\Windows",
        os.environ.get("ProgramFiles") or r"C:\Program Files",
        os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)",
        r"C:\ProgramData",
        # The project folder that this assistant lives in.
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ]
else:
    _PROTECTED_ROOTS = [
        "/etc", "/usr", "/bin", "/sbin", "/boot", "/lib", "/lib64",
        "/root", "/sys", "/proc", "/dev", "/var", "/opt",
        # The project folder that this assistant lives in.
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ]

# The scan result cache: holds the list of suspicious files found by the most
# recent scan so `clean` knows exactly what was reported (and re-validates
# that those exact files are still there / not protected).
_LAST_SCAN_FILES: list[str] = []
_scan_lock = threading.Lock()
_scan_cancelled = threading.Event()
_whitelist: set[str] = set()


def _allowed_scan_roots():
    """Folders we actually scan by default (user data, not system)."""
    home = Path.home()
    roots = []
    subs = ("Downloads", "Desktop", "Documents", "Music", "Pictures",
            "Videos")
    if os.name == "nt":
        subs += ("AppData/Local/Temp",)
    else:
        subs += (".cache",)
    for sub in subs:
        p = home / sub
        if p.exists():
            roots.append(str(p))
    return roots


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _report(player, msg):
    try:
        player.write_log(f"GANDALF: {msg}")
    except Exception:
        pass
    return msg


def _speak(msg, player):
    try:
        edge_speak(msg, player)
    except Exception:
        pass


def _is_protected(path: str) -> bool:
    """True if a path is inside a protected root (never removable)."""
    try:
        p = os.path.abspath(path).lower()
    except Exception:
        return True
    for root in _PROTECTED_ROOTS:
        try:
            r = os.path.abspath(root).lower().rstrip("\\/")
        except Exception:
            continue
        if p == r or p.startswith(r + os.sep):
            return True
    return False


def _in_whitelist(path: str) -> bool:
    try:
        p = os.path.abspath(path).lower()
    except Exception:
        return False
    for w in _whitelist:
        try:
            wl = os.path.abspath(w).lower().rstrip("\\/")
        except Exception:
            continue
        if p == wl or p.startswith(wl + os.sep):
            return True
    return False


def _heuristic_risk(path: str) -> int:
    """Return a risk score 0-100 for a single file path (pure heuristics)."""
    name = os.path.basename(path).lower()
    ext = os.path.splitext(name)[1].lower()

    if ext not in _SUSPICIOUS_EXT:
        return 0

    score = 20
    if ext in _HIGH_RISK_EXT:
        score += 20

    if name in _KNOWN_BAD_NAMES:
        score += 40
    if any(tok in name for tok in _SUSPICIOUS_NAME_TOKENS):
        score += 25

    # Double-extension camouflage (e.g. "invoice.pdf.exe").
    dot_count = name.count(".") - 1  # minus one for the real ext
    if dot_count >= 1 and ext in _HIGH_RISK_EXT:
        score += 15

    # Where it lives matters a lot.
    path_l = path.lower().replace("/", os.sep)
    if (os.sep + "desktop") in path_l or (os.sep + "downloads") in path_l \
            or (os.sep + "temp") in path_l or (os.sep + "tmp") in path_l \
            or (os.sep + "cache") in path_l:
        score += 15
    if ".config" in path_l or "appdata" in path_l:
        score += 10
    return min(100, score)


def _walk_files(root, cancelled):
    """Yield suspicious files under a root, honouring cancellation."""
    hits = []
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            if cancelled.is_set():
                return hits
            # Don't descend into protected system trees.
            dirnames[:] = [
                d for d in dirnames
                if not _is_protected(os.path.join(dirpath, d))
            ]
            for fn in filenames:
                if cancelled.is_set():
                    return hits
                full = os.path.join(dirpath, fn)
                if _is_protected(full) or _in_whitelist(full):
                    continue
                risk = _heuristic_risk(full)
                if risk >= 35:  # only report meaningful hits
                    hits.append((full, risk))
        return hits
    except Exception:
        return hits


def _signature_scan(root, cancelled):
    """Run the platform signature scanner on a folder.

    Windows -> MpCmdRun.exe (Windows Defender). Linux -> clamscan (ClamAV).
    Returns a dict {threat_name: [paths]}. If the tool is missing or fails it
    returns {} (heuristics still apply).
    """
    if os.name == "nt":
        return _defender_scan(root, cancelled)
    return _clamav_scan(root, cancelled)


def _defender_scan(root, cancelled):
    """Run a (quick) Windows Defender scan of a folder; collect detected paths."""
    mp = _find_mpcmdrun()
    if not mp:
        return {}
    try:
        proc = subprocess.Popen(
            [mp, "-Scan", "-ScanType", "3", "-ScanPath", root],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            out, _ = proc.communicate(timeout=240)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            return {}
        text = (out or b"").decode("utf-8", "replace")
    except Exception:
        return {}

    found = {}
    for line in text.splitlines():
        if cancelled.is_set():
            break
        line = line.strip()
        if line.lower().startswith("name:"):
            name = line.split(":", 1)[1].strip() if ":" in line else ""
            if name:
                found.setdefault(name, [])
        elif line.lower().startswith("path:"):
            p = line.split(":", 1)[1].strip() if ":" in line else ""
            if p:
                if found:
                    last = next(reversed(found))
                    if p not in found[last]:
                        found[last].append(p)
    return found


def _clamav_scan(root, cancelled):
    """Run clamscan recursively; collect infected paths.

    Output lines look like:  /path/to/file: Win.Adware.Something FOUND
    Returns {threat_name: [paths]}.
    """
    if not shutil.which("clamscan"):
        return {}
    try:
        proc = subprocess.Popen(
            ["clamscan", "-r", "-i", "--no-summary", root],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            out, _ = proc.communicate(timeout=600)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            return {}
        text = (out or b"").decode("utf-8", "replace")
    except Exception:
        return {}

    found = {}
    for line in text.splitlines():
        if cancelled.is_set():
            break
        line = line.strip()
        if not line.endswith(" FOUND"):
            continue
        path_part = line[:-len(" FOUND")]
        if ": " in path_part:
            p, _, threat = path_part.rpartition(": ")
        else:
            p = line
            threat = "Suspicious"
        if os.path.exists(p) and not _is_protected(p):
            found.setdefault(threat or "Threat", []).append(p)
    return found


def _find_mpcmdrun():
    """Locate MpCmdRun.exe (Windows Defender CLI)."""
    pf = os.environ.get("ProgramFiles") or "C:\\Program Files"
    cand = os.path.join(pf, "Windows Defender", "MpCmdRun.exe")
    return cand if os.path.isfile(cand) else None


# ---------------------------------------------------------------------------
# Trash / Recycle Bin removal — safe, recoverable delete
# ---------------------------------------------------------------------------

def _to_recycle_bin(paths: list) -> tuple[int, list]:
    """Send files/folders to the Windows Recycle Bin via SHFileOperation."""
    import ctypes
    removed = 0
    failed = []

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", ctypes.c_void_p),
            ("wFunc", ctypes.c_uint),
            ("pFrom", ctypes.c_wchar_p),
            ("pTo", ctypes.c_wchar_p),
            ("fFlags", ctypes.c_uint),
            ("fAnyOperationsAborted", ctypes.c_int),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", ctypes.c_wchar_p),
        ]

    for p in paths:
        try:
            p = os.path.abspath(p)
            if not os.path.exists(p):
                continue
            if _is_protected(p):
                failed.append(p)
                continue
            FO_DELETE = 0x0003
            FOF_SILENT = 0x0004
            FOF_NOCONFIRMATION = 0x0010
            FOF_ALLOWUNDO = 0x0040  # send to Recycle Bin
            FOF_NOERRORUI = 0x0400

            p_from = ctypes.create_unicode_buffer(p + "\x00\x00")

            op = SHFILEOPSTRUCTW()
            op.hwnd = None
            op.wFunc = FO_DELETE
            op.pFrom = ctypes.cast(p_from, ctypes.c_wchar_p)
            op.pTo = None
            op.fFlags = FOF_ALLOWUNDO | FOF_SILENT | FOF_NOCONFIRMATION | FOF_NOERRORUI
            op.fAnyOperationsAborted = 0
            op.hNameMappings = None
            op.lpszProgressTitle = None

            shell32 = ctypes.windll.shell32
            shell32.SHFileOperationW.argtypes = [ctypes.c_void_p]
            shell32.SHFileOperationW.restype = ctypes.c_int
            result = shell32.SHFileOperationW(ctypes.byref(op))
            if result == 0 and not os.path.exists(p):
                removed += 1
            else:
                failed.append(p)
        except Exception:
            failed.append(p)
    return removed, failed


def _trash_home() -> Path:
    """FreeDesktop.org Trash directory (~/.local/share/Trash)."""
    data_home = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(data_home) / "Trash"


def _to_linux_trash(paths: list) -> tuple[int, list]:
    """Move paths into the FreeDesktop.org trash (never permanent)."""
    removed = 0
    failed = []
    trash = _trash_home()
    files_dir = trash / "files"
    info_dir = trash / "info"
    try:
        files_dir.mkdir(parents=True, exist_ok=True)
        info_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return 0, paths

    for p in paths:
        try:
            p = os.path.abspath(p)
            if not os.path.exists(p):
                continue
            if _is_protected(p):
                failed.append(p)
                continue
            name = os.path.basename(p.rstrip("/"))
            dest = files_dir / name
            i = 1
            while dest.exists():
                dest = files_dir / f"{name}.{i}"
                i += 1
            moved = False
            try:
                shutil.move(p, str(dest))
                moved = True
            except Exception:
                # Cross-device fallback: copy then delete.
                try:
                    if os.path.isdir(p):
                        shutil.copytree(p, str(dest))
                        shutil.rmtree(p)
                    else:
                        shutil.copy2(p, str(dest))
                        os.remove(p)
                    moved = True
                except Exception:
                    pass
            if not moved:
                failed.append(p)
                continue
            # Write the .trashinfo metadata (deletion date).
            date = time.strftime("%Y-%m-%dT%H:%M:%S")
            meta = (f"[Trash Info]\nPath={p}\nDeletionDate={date}\n"
                    .replace(" ", "%20"))
            try:
                (info_dir / f"{os.path.basename(str(dest))}.trashinfo") \
                    .write_text(meta, encoding="utf-8")
            except Exception:
                pass
            removed += 1
        except Exception:
            failed.append(p)
    return removed, failed


def _remove_to_trash(paths: list) -> tuple[int, list]:
    if os.name == "nt":
        return _to_recycle_bin(paths)
    return _to_linux_trash(paths)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_security(parameters, response=None, player=None, session_memory=None):
    action = str((parameters or {}).get("action", "")).strip().lower()
    msg = ""

    if action.startswith("scan"):
        msg = _do_scan(action, parameters, player, session_memory)
    elif action.startswith("clean"):
        msg = _do_clean(action, parameters, player, session_memory)
    elif action.startswith("status"):
        msg = _signature_status()
    elif action.startswith("stop"):
        _scan_cancelled.set()
        msg = "Scan cancelled, sir."
    elif action.startswith("whitelist"):
        target = action.split(":", 1)[1].strip() if ":" in action else ""
        if target:
            _whitelist.add(os.path.abspath(target).lower())
            msg = f"Whitelisted {target}. It will be skipped in future scans."
        else:
            msg = "Sir, tell me which folder to whitelist."
    else:
        msg = ("Sir, I can scan, clean (with your confirmation), show scan "
               "status, stop a scan, or whitelist a folder. What would you "
               "like?")

    _report(player, msg)
    _speak(msg, player)
    return msg


def _do_scan(action, parameters, player, session_memory):
    global _LAST_SCAN_FILES
    _scan_cancelled.clear()

    # Optional explicit path, but never allow scanning protected/system roots.
    target = ""
    if ":" in action:
        target = action.split(":", 1)[1].strip()
    if target and not os.path.exists(target):
        return "Sir, that scan path does not exist."
    if target and _is_protected(target):
        return ("Sir, I can't scan system-folders directly for your safety. "
                "I'll scan your user folders instead.")

    roots = [target] if target else _allowed_scan_roots()
    if not roots and not target:
        # Nothing found under user profile — fall back to scanning downloads.
        dl = str(Path.home() / "Downloads")
        roots = [dl] if Path(dl).exists() else []

    hits = {}
    signature = {}

    for root in roots:
        _report(player, f"Scanning {root} ... (say 'stop' to cancel)")
        # Heuristic pass.
        try:
            list_hits = _walk_files(root, _scan_cancelled)
        except Exception:
            list_hits = []
        for full, risk in list_hits:
            hits[full] = max(hits.get(full, 0), risk)

        # Signature pass — only if this is a folder.
        if os.path.isdir(root):
            try:
                res = _signature_scan(root, _scan_cancelled)
            except Exception:
                res = {}
            signature.update(res)

    # Signature-reported paths are treated as highest-risk regardless of ext.
    for threat, paths in signature.items():
        for p in paths:
            if p and not _is_protected(p):
                hits[p] = max(hits.get(p, 0), 100)

    # Persist the *confirmed* hit list (skips whitelisted / protected).
    _LAST_SCAN_FILES = sorted(
        (p for p in hits if os.path.exists(p) and not _in_whitelist(p)),
        key=lambda p: hits[p], reverse=True,
    )

    if _scan_cancelled.is_set():
        _report(player, "Scan cancelled.")
        return "Scan cancelled, sir."

    if not _LAST_SCAN_FILES:
        return "Scan complete. I found nothing suspicious in the scan folders, sir."

    lines = [f"Scan complete. I found {len(_LAST_SCAN_FILES)} suspicious item(s):"]
    for i, p in enumerate(_LAST_SCAN_FILES[:30], 1):
        lines.append(f"  {i}. {p}  (risk {hits.get(p, 0)}/100)")
    if len(_LAST_SCAN_FILES) > 30:
        lines.append(f"  ... and {len(_LAST_SCAN_FILES) - 30} more.")

    # Ask for confirmation before ANY removal.
    if session_memory is not None:
        try:
            session_memory.set_pending_intent("security")
            session_memory.update_parameters({"action": "clean", "confirm": None})
            session_memory.set_current_question("confirm")
        except Exception:
            pass

    lines.append("")
    lines.append("Shall I send these to the Trash? (say 'yes, remove them')")
    return "\n".join(lines)


def _do_clean(action, parameters, player, session_memory):
    global _LAST_SCAN_FILES
    # NEVER remove without an explicit, recent confirmation.
    confirm = str((parameters or {}).get("confirm") or "").strip().lower()
    yes_markers = ("yes", "evet", "onaylıyorum", "temizle", "sil", "do it",
                   "remove", "confirm", "onay")
    confirm_words = set(re.split(r"[\W_]+", confirm))
    ok = (confirm in yes_markers or confirm_words.intersection(yes_markers)
          or action == "clean:yes")
    if not ok:
        if not _LAST_SCAN_FILES:
            return "Sir, there's nothing pending to clean. Run a scan first."
        return ("I'll only remove files after you confirm. "
                "Say 'yes, remove them' and I'll send them to the Trash.")

    targets = [p for p in _LAST_SCAN_FILES if os.path.exists(p)]
    # Re-validate protection at removal time (guard against race / mistakes).
    safe = [p for p in targets if not _is_protected(p)]
    skipped_protected = len(targets) - len(safe)

    if not safe:
        return "Nothing left to remove (they may already be gone or protected)."

    removed, failed = _remove_to_trash(safe)
    _LAST_SCAN_FILES = []

    parts = [f"Moved {removed} item(s) to the Trash, sir."]
    if failed:
        parts.append(f"Couldn't move {len(failed)} (in use or protected).")
    if skipped_protected:
        parts.append(f"Skipped {skipped_protected} protected item(s).")
    return " ".join(parts)


def _signature_status():
    if os.name == "nt":
        mp = _find_mpcmdrun()
        if not mp:
            return "Windows Defender CLI not found on this system, sir."
        try:
            proc = subprocess.Popen(
                [mp, "-GetFiles"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            out, _ = proc.communicate(timeout=30)
            text = (out or b"").decode("utf-8", "replace")
            if "not configured" in text.lower() or "service" in text.lower():
                return "Defender reports the service is not running / not configured."
            return "Windows Defender is configured and running, sir."
        except Exception:
            return "I couldn't query Windows Defender status, sir."

    if not shutil.which("clamscan"):
        return ("ClamAV (clamscan) is not installed on this system, sir. "
                "Install it with: sudo apt install clamav clamav-daemon")
    try:
        out = subprocess.run(["clamscan", "--version"], shell=False,
                             capture_output=True, timeout=20, text=True)
        info = (out.stdout or "").splitlines()[0] if out.stdout else "ClamAV"
        fresh = subprocess.run(["systemctl", "is-active", "clamav-freshclam"],
                               shell=False, capture_output=True, timeout=20,
                               text=True)
        state = (fresh.stdout or "").strip()
        if state == "active":
            return f"{info} is active with fresh signatures, sir."
        return (f"{info} is installed, sir, but the signature updater "
                "(clamav-freshclam) is not currently running.")
    except Exception:
        return "I couldn't query the ClamAV status, sir."