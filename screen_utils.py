# -*- coding: utf-8 -*-
"""Ekran uzaktan aç/kapa (telegram_bot.py'den çağrılır).

  - set_monitor_power(on) : monitörü anında kapatır/açar (uygulamalar devam eder).

  Windows -> SC_MONITORPOWER + SetThreadExecutionState.
  Linux   -> "xset dpms force off/on" (X11). Wayland'da bu tool yoksa işlem
             graciously devre dışı kalır (bir hata fırlatmaz).
"""
import os
import shutil
import subprocess


def _set_display_required(on: bool) -> None:
    """Windows güç yöneticisine ekran kullanım durumunu bildir."""
    if os.name != "nt":
        return
    try:
        import ctypes
        _ES_DISPLAY_REQUIRED = 0x00000001   # ekran "kullanımda" -> kapatılmamalı
        _ES_CONTINUOUS = 0x80000000         # kalıcı işaret
        flags = (_ES_DISPLAY_REQUIRED | _ES_CONTINUOUS) if on else _ES_CONTINUOUS
        ctypes.windll.kernel32.SetThreadExecutionState(flags)
    except Exception:
        pass


def _send_fake_input() -> None:
    """Idle zamanlayıcısını sıfırlamak için görünmez bir mouse hareketi gönder."""
    if os.name != "nt":
        return
    try:
        import ctypes
        MOUSEEVENTF_MOVE = 0x0001
        ctypes.windll.user32.mouse_event(MOUSEEVENTF_MOVE, 1, 0, 0, 0)
        ctypes.windll.user32.mouse_event(MOUSEEVENTF_MOVE, -1, 0, 0, 0)
    except Exception:
        pass


def _linux_xset(sub: str) -> bool:
    try:
        subprocess.run(["xset", "dpms", "force", sub],
                       shell=False, check=True, capture_output=True, timeout=10)
        return True
    except Exception:
        return False


def set_monitor_power(on: bool) -> bool:
    """Monitörü kapat/aç. Sistem uykuya geçmez; uygulamalar çalışmaya devam eder.

    Windows: ekran açılırken önce "ES_DISPLAY_REQUIRED" işareti konur, ekran
    uyandırılır ve görünmez bir mouse hareketi ile "son kullanım" yenilenir
    (güç planı hemen geri kapatmaz). Kapatınca işaret temizlenir.
    Linux: xset dpms force off/on (sadece X11 oturumlarında; Wayland'da tool
    yoksa False döner).
    """
    try:
        if os.name != "nt":
            return _linux_xset("on" if on else "off")
        import ctypes
        WM_SYSCOMMAND = 0x0112
        SC_MONITORPOWER = 0xF170
        if on:
            _set_display_required(True)
            wparam = -1
            ctypes.windll.user32.SendMessageW(0xFFFF, WM_SYSCOMMAND, SC_MONITORPOWER, wparam)
            _send_fake_input()
        else:
            _set_display_required(False)
            wparam = 2
            ctypes.windll.user32.SendMessageW(0xFFFF, WM_SYSCOMMAND, SC_MONITORPOWER, wparam)
        return True
    except Exception:
        return False