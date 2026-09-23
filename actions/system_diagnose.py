"""System status & diagnostics for Gandalf (Read-only).

Uses psutil (already a dependency) for resource/sensor data and ctypes for
screen capture. Nothing here changes system state — it only reports.

Supported actions (parameters.action):
    system_status       overall health: CPU, RAM, disk, battery, uptime
    cpu                 CPU usage (and per-core if available)
    ram                 RAM used/total/percent
    disk                disk space per drive
    battery             battery level / charging state
    network             network interfaces with addresses + traffic
    system_info         OS, machine, CPU name, hostname
    uptime              how long the machine has been running
    processes           top CPU/memory processes (top 15)
    screenshot[:path]   take a screenshot (returns the saved PNG path)
"""

import os
import time
import ctypes
import platform
import subprocess

from tts import edge_speak


def _report(player, msg):
    try:
        player.write_log(f"GANDALF: {msg}")
    except Exception:
        pass
    return msg


def _f(n, nd=1):
    try:
        if n is None:
            return "0"
        return f"{float(n):.{nd}f}"
    except Exception:
        return str(n)


def _pmem() -> object:
    import psutil
    return psutil


def _collect(action, extra=None):
    """Return a formatted string for the requested diagnostic."""
    ps = _pmem()
    action = action.strip().lower()

    if action == "system_status":
        cpu = ps.cpu_percent(interval=0.5)
        mem = ps.virtual_memory()
        disk = None
        disk_label = "/"
        if os.name == "nt" and os.path.exists("C:\\"):
            disk = ps.disk_usage("C:\\")
            disk_label = "C:"
        else:
            for part in ps.disk_partitions(all=True):
                if part.mountpoint == "/":
                    try:
                        disk = ps.disk_usage(part.mountpoint)
                    except Exception:
                        disk = None
                    break
        boot = ps.boot_time()
        up_secs = time.time() - boot
        up = f"{int(up_secs // 3600)}h {int((up_secs % 3600) // 60)}m"
        batt = ps.sensors_battery()
        btxt = f"{batt.percent}% ({'charging' if batt.power_plugged else 'on battery'})" \
               if batt else "n/a"
        lines = [
            f"CPU: {cpu}%",
            f"RAM: {mem.used / 2 ** 30:.1f} / {mem.total / 2 ** 30:.1f} GB "
            f"({mem.percent}%)",
        ]
        if disk:
            lines.append(f"Disk {disk_label}: {disk.used / 2 ** 30:.0f} / "
                         f"{disk.total / 2 ** 30:.0f} GB free "
                         f"({100 - disk.percent:.0f}%)")
        lines.append(f"Battery: {btxt}")
        lines.append(f"Uptime: {up}")
        return "\n".join(lines)

    if action == "cpu":
        return f"CPU usage: {ps.cpu_percent(interval=0.5)}% (cores: "\
               f"{', '.join(str(c) for c in ps.cpu_percent(interval=0.5, percpu=True))})"
    if action == "ram":
        mem = ps.virtual_memory()
        return (f"RAM: {mem.used / 2 ** 30:.1f} / {mem.total / 2 ** 30:.1f} GB "
                f"used, {mem.percent}%.")
    if action == "disk":
        lines = []
        for part in ps.disk_partitions():
            try:
                u = ps.disk_usage(part.mountpoint)
                lines.append(f"{part.device} ({part.fstype}): "
                             f"{u.used / 2 ** 30:.0f}/{u.total / 2 ** 30:.0f} GB "
                             f"({u.percent:.0f}%)")
            except Exception:
                continue
        return "\n".join(lines) if lines else "No fixed drives found."
    if action == "battery":
        batt = ps.sensors_battery()
        if not batt:
            return "No battery detected (desktop?)."
        state = "charging" if batt.power_plugged else "on battery"
        if batt.secsleft and batt.secsleft >= 0:
            return (f"Battery: {batt.percent}%, {state}; "
                    f"~{int(batt.secsleft / 60)} min left.")
        return f"Battery: {batt.percent}%, {state}."
    if action == "network":
        lines = []
        for name, addrs in ps.net_if_addrs().items():
            ip = next((a.address for a in addrs if a.family == 2), None)
            if ip:
                lines.append(f"{name}: {ip}")
        return "\n".join(lines) if lines else "No network interfaces with IPs found."
    if action == "system_info":
        return (f"{platform.system()} {platform.release()} ({platform.machine()}), "
                f"host: {platform.node()}, "
                f"CPU: {platform.processor() or 'unknown'}")
    if action == "uptime":
        up = time.time() - ps.boot_time()
        return (f"The machine has been up for {int(up // 3600)} hours and "
                f"{int((up % 3600) // 60)} minutes.")
    if action == "processes":
        proc = (p for p in ps.process_iter(["name", "cpu_percent", "memory_percent"])
                if p.info is not None)
        top = sorted(proc, key=lambda p: p.info.get("cpu_percent") or 0,
                     reverse=True)[:15]
        return "\n".join(
            f"{p.info.get('name') or p.name() or '?'}: {_f(p.info.get('cpu_percent'))}% CPU, "
            f"{_f(p.info.get('memory_percent'))}% mem" for p in top
        ) or "No process data available."
    if action == "screenshot":
        return _take_screenshot(extra)
    return "Sir, I don't recognise that diagnostic."


def _take_screenshot(extra=None):
    """Capture the full screen: pyautogui first, then a platform tool.
    Windows fallback -> ctypes BitBlt; Linux fallback -> scrot / gnome-screenshot."""
    out = extra or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "screenshots",
                                f"diag_{int(time.time())}.png")
    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        try:
            import pyautogui
            pyautogui.screenshot().save(out)
            return out
        except Exception:
            pass
        if os.name == "nt":
            return _bitblt_screenshot(out)
        for tool in (["scrot", out], ["gnome-screenshot", "-f", out],
                     ["import", "-window", "root", out]):
            try:
                subprocess.run(tool, check=True, timeout=20,
                               capture_output=True)
                return out
            except Exception:
                continue
        return "Screenshot failed: no screenshot tool available."
    except Exception as e:
        return f"Screenshot failed: {e}"


def _bitblt_screenshot(out):
    try:
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        w = user32.GetSystemMetrics(0)
        h = user32.GetSystemMetrics(1)
        hdc = user32.GetDC(0)
        mdc = gdi32.CreateCompatibleDC(hdc)
        bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
        gdi32.SelectObject(mdc, bmp)
        gdi32.BitBlt(mdc, 0, 0, w, h, hdc, 0, 0, 0x00CC0020)
        # Save via PIL if available.
        from PIL import Image
        # GetDIBits path:
        from ctypes import wintypes
        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                        ("biClrImportant", wintypes.DWORD)]
        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = w
        bmi.biHeight = -h  # top-down
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bmi), 0)
        img = Image.frombuffer("RGB", (w, h), buf.raw, "raw", "BGRX", 0, 1)
        img.save(out)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        user32.ReleaseDC(0, hdc)
        return out
    except Exception as e:
        return f"Screenshot failed: {e}"


def run_system_diagnose(parameters, response=None, player=None, session_memory=None):
    action = str((parameters or {}).get("action", "")).strip()
    extra = str((parameters or {}).get("path") or "").strip() or None
    if action.startswith("screenshot") and extra is None:
        extra = str((parameters or {}).get("target") or "").strip() or None
    if not action:
        action = "system_status"
    msg = _collect(action, extra)
    _report(player, msg)
    return msg
