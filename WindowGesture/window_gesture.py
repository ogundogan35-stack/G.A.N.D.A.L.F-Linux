# -*- coding: utf-8 -*-
"""
WindowGesture (Linux) — El ile pencere yönetimi + "the hands" birleşik modu.

Linux sürümü: Windows güdümlü GDI/ctypes kodunun yerine geçer. Aynı CLI ve
ara yüz korunur:

  python window_gesture.py            -> yalnızca pencere yönetimi
  python window_gesture.py --hands    -> birleşik: SAĞ el pinç parlaklık,
                                         SOL el pinç ses + pencere yönetimi

Altyapı:
  - El tespiti: mediapipe Hands + OpenCV (webcam önizleme penceresi ve iskelet
    çizimi — bu, Windows'taki ekran-üstü overlay'in yerine geçer).
  - Fare / pencere eylemleri: xdotool + wmctrl (setup.sh bunları kurar).
  - Parlaklık: brightnessctl. Ses: pactl / amixer.

Kapatmak için: webcam penceresinde q veya ESC. --hands modunda pystray
menüsünden "Exit" de çalışır (pystray kuruluysa).

Araçlardan biri eksikse ya da kamera açılamazsa, süreç bir hata mesajı yazıp
GRACEFUL şekilde çıkar (hand_control.py script'in başlamadığını görür).
"""
import os
import sys
import time
import math
import shutil
import threading
import subprocess

HANDS_MODE = "--hands" in sys.argv

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "window_gesture.log")


def log_event(msg):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Tek örnek kilidi (Linux: /tmp kilidi — PID dosyası O_EXCL ile oluşturulur,
# süreç bitince kaldırılır; sistem önyüklemesinde /tmp temizlenir).
# ---------------------------------------------------------------------------
_HANDS_LOCK_PATH = "/tmp/window_gesture_hands.lock"
_hands_lock_fd = None


def _acquire_single_lock() -> bool:
    if not HANDS_MODE:
        return True
    global _hands_lock_fd
    try:
        fd = os.open(_HANDS_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        os.write(fd, str(os.getpid()).encode())
        _hands_lock_fd = fd
        return True
    except OSError:
        return False


def _release_single_lock():
    global _hands_lock_fd
    if _hands_lock_fd is not None:
        try:
            os.close(_hands_lock_fd)
        except Exception:
            pass
        _hands_lock_fd = None
    try:
        os.unlink(_HANDS_LOCK_PATH)
    except Exception:
        pass


HANDS_LOCK_OK = _acquire_single_lock()
if HANDS_MODE and not HANDS_LOCK_OK:
    print("Another 'the hands' instance is already running; exiting.")
    log_event("exit: another --hands instance already running")
    sys.exit(0)


# ---------------------------------------------------------------------------
# System tray (yalnızca --hands; pystray kuruluysa. Kurulu değilse yok sayılır.)
# ---------------------------------------------------------------------------
_tray_icon = None
_tray_exit_flag = threading.Event()


def _start_tray():
    global _tray_icon
    try:
        import pystray
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse((18, 30, 46, 62), fill=(34, 177, 76, 255))
        d.ellipse((34, 6, 58, 30), fill=(34, 177, 76, 255))
        d.rectangle((18, 18, 24, 40), fill=(34, 177, 76, 255))
        d.rectangle((26, 12, 32, 40), fill=(34, 177, 76, 255))
        d.rectangle((34, 16, 40, 42), fill=(34, 177, 76, 255))
        d.rectangle((42, 24, 48, 46), fill=(34, 177, 76, 255))
        icon = pystray.Icon("WindowGesture", img,
                            "The Hands — brightness/volume + window control")
        icon.menu = pystray.Menu(
            pystray.MenuItem("Hand control ACTIVE", None, enabled=False),
            pystray.MenuItem("Exit", lambda: _tray_exit_flag.set()),
        )
        _tray_icon = icon
        threading.Thread(target=icon.run, daemon=True).start()
    except Exception:
        _tray_icon = None


# ---------------------------------------------------------------------------
# Sistem araçları yardımcıları (tümü best-effort, subprocess üzerinden)
# ---------------------------------------------------------------------------

def _run(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, shell=False, capture_output=True, timeout=timeout)
        return r.returncode, (r.stdout or b"").decode("utf-8", "ignore")
    except Exception as e:
        print("WINDOWGESTURE TOOL ERROR:", e)
        return -1, ""


def _have(tool):
    return shutil.which(tool) is not None


def _screen_size():
    """(w, h) — xrandr (birincil) veya pyautogui veya varsayılan."""
    try:
        import pyautogui
        w, h = pyautogui.size()
        return int(w), int(h)
    except Exception:
        pass
    code, out = _run(["xrandr", "--query"])
    if code == 0:
        import re
        m = re.search(r"(\d+)x(\d+)", out)
        if m:
            return int(m.group(1)), int(m.group(2))
    return 1920, 1080


def _move_mouse(x, y):
    if _have("xdotool"):
        _run(["xdotool", "mousemove", str(int(x)), str(int(y))])


def _click(button="1"):
    if _have("xdotool"):
        _run(["xdotool", "click", button])


def _window_under_cursor():
    """xdotool getmouselocation --shell -> WINDOW id veya None."""
    if not _have("xdotool"):
        return None
    code, out = _run(["xdotool", "getmouselocation", "--shell"])
    if code != 0:
        return None
    for line in out.splitlines():
        if line.startswith("WINDOW="):
            val = line.split("=", 1)[1].strip()
            if val.isdigit() and int(val) > 0:
                return val
    return None


def _window_geometry(wid):
    """(x, y, w, h) of a window id."""
    code, out = _run(["xdotool", "getwindowgeometry", "--shell", wid])
    if code != 0:
        return None
    geo = {}
    for line in out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            geo[k.strip()] = int(v.strip())
    if geo.get("WIDTH") and geo.get("HEIGHT"):
        return (geo.get("X", 0), geo.get("Y", 0),
                geo["WIDTH"], geo["HEIGHT"])
    return None


def _wmctrl_geometry(wid, x, y, w, h):
    _run(["wmctrl", "-i", "-r", wid, "-e", f"0,{int(x)},{int(y)},{int(w)},{int(h)}"])


def _close_window(wid):
    _run(["wmctrl", "-ic", wid])


# Doğrudan ayarlar (--hands; eksikse yok sayılır)
def set_brightness_percent(pct):
    pct = max(0, min(100, int(pct)))
    if _have("brightnessctl"):
        code, _ = _run(["brightnessctl", "-s", "set", f"{pct}%"])
        if code == 0:
            return True
    return False


def get_brightness_percent():
    if _have("brightnessctl"):
        _, out = _run(["brightnessctl", "get"])
        _, mx = _run(["brightnessctl", "max"])
        try:
            cur, maxv = int(out.strip()), int(mx.strip())
            if maxv > 0:
                return (cur * 100.0) / maxv
        except Exception:
            pass
    return 50.0


# Pactl ses seviyesi: "Sink: ..." akışının "volume: front-left: n / 65536"
def _parse_pactl_volume(out):
    try:
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("volume:"):
                parts = line.split(",")
                first = parts[0].replace("volume:", "").strip()
                # "front-left: 30000 /  46%" -> yüzdeli kısmı al
                if "/" in first:
                    pct = first.split("/")[-1].strip().rstrip("%")
                    return float(pct)
    except Exception:
        pass
    return None


def get_volume_percent():
    code, out = _run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
    if code == 0:
        v = _parse_pactl_volume(out)
        if v is not None:
            return v
    return 50.0


def set_volume_percent(pct):
    pct = max(0, min(100, int(pct)))
    code, _ = _run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{pct}%"])
    if code == 0:
        return True
    return _run(["amixer", "-q", "set", "Master", f"{pct}%"])[0] == 0


# ---------------------------------------------------------------------------
# Mediapipe el tespiti (zorunlu; eksikse graceful çıkış)
# ---------------------------------------------------------------------------

def _load_mediapipe():
    global mp, mp_hands
    import mediapipe as _mp
    mp = _mp
    mp_hands = _mp.solutions.hands

TIPS = {4, 8, 12, 16, 20}
HAND_CONNS = None


def _finger_distance(a_lmpt, b_lmpt, palm):
    """Pinç mesafesi 0..1 (avuç boyutuna göre normalize)."""
    if palm <= 0:
        return 1.0
    return math.dist((a_lmpt.x, a_lmpt.y), (b_lmpt.x, b_lmpt.y)) / palm


def _palm_size(lms):
    """İşaret-orta parmak kökleri arası mesafe (referans)."""
    a, b = lms[5], lms[9]
    return math.dist((a.x, a.y), (b.x, b.y))


def _is_pinch(lms, thumb=4, index=8, thresh=0.28):
    palm = _palm_size(lms)
    return _finger_distance(lms[thumb], lms[index], palm) < thresh


# ---------------------------------------------------------------------------
# Ana döngü
# ---------------------------------------------------------------------------

def main():
    log_event("starting (linux%s)" % (" --hands" if HANDS_MODE else ""))

    for tool in ("xdotool", "wmctrl"):
        if not _have(tool):
            log_event(f"fatal: {tool} not installed (missing system tool)")
            print(f"Missing required tool: {tool}. Install it with setup.sh.")
            sys.exit(1)

    try:
        _load_mediapipe()
    except Exception as e:
        log_event(f"fatal: mediapipe import failed ({e})")
        print("mediapipe not available:", e)
        sys.exit(1)

    cap = None
    landmarker = None
    try:
        cap = __import__("cv2").VideoCapture(0)
        if not cap.isOpened():
            raise RuntimeError("camera not available")
    except Exception as e:
        log_event(f"fatal: camera not available ({e})")
        print("Camera not available:", e)
        sys.exit(1)

    landmarker = mp_hands.Hands(
        static_image_mode=False,
        max_num_hands=2 if HANDS_MODE else 1,
        min_detection_confidence=0.55,
        min_tracking_confidence=0.5,
    )

    SCREEN_W, SCREEN_H = _screen_size()
    print(f"Screen {SCREEN_W}x{SCREEN_H}")

    _start_tray()

    # Gesture state
    cursor_pt = None
    grab_active = False
    grab_wid = None
    grab_mode = "move"          # "move" yahut "resize"
    grab_start_cursor = (0, 0)
    grab_start_hand = (0, 0)
    grab_start_geo = None
    grab_last_ynorm = None
    grab_last_t = 0.0
    grab_begin_t = 0.0
    click_fire = False          # orta+başparmak tutuklama
    click_prev_pinch = False
    last_action = ("", 0.0)

    # pinç -> parlaklık/ses (--hands)
    prev_r_pinch = None
    prev_l_pinch = None
    last_apply = 0.0

    def to_screen(xn, yn):
        return (xn * SCREEN_W, yn * SCREEN_H)

    def pick_hand(result):
        """Takip edilecek el (en büyük = en yakın)."""
        hands = result.multi_hand_landmarks or []
        if not hands:
            return None, None
        best = None
        best_size = -1
        for i, lms in enumerate(hands):
            s = _palm_size(lms)
            if s > best_size:
                best_size = s
                best = i
        try:
            label = (result.multi_handedness[best].classification[0].label
                     if result.multi_handedness else "Right")
        except Exception:
            label = "Right"
        return hands[best], label

    import cv2
    frame_idx = 0
    try:
        while not _tray_exit_flag.is_set():
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.1)
                continue
            frame_idx += 1
            now = time.time()
            frame = cv2.flip(frame, 1)  # aynalama
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            result = landmarker.process(rgb)

            hand_pts = []
            if result.multi_hand_landmarks:
                # İskelet çiz
                for lms in result.multi_hand_landmarks:
                    pts = []
                    for lm in lms:
                        pts.append((int((1.0 - lm.x) * frame.shape[1]),
                                    int(lm.y * frame.shape[0])))
                    hand_pts.append(pts)
                    for a, b in HAND_CONNS:
                        p1, p2 = pts[a], pts[b]
                        cv2.line(frame, p1, p2, (0, 255, 0), 2)
                    for i, p in enumerate(pts):
                        cv2.circle(frame, p, 5,
                                   (0, 0, 255) if i in TIPS else (255, 0, 0), -1)

                # Tek bir takip elinin eylemleri
                lms, hand_label = pick_hand(result)
                if lms is None:
                    cursor_pt = None
                else:
                    mx = 1.0 - lms[8].x   # aynalanmış x
                    my = lms[8].y
                    cursor_pt = to_screen(mx, my)
                    _move_mouse(cursor_pt[0], cursor_pt[1])

                    grab_now = _is_pinch(lms, 4, 8, 0.28)
                    click_now = _is_pinch(lms, 4, 12, 0.30) and \
                        not _is_pinch(lms, 4, 8, 0.22)

                    # Fingers'ın açıklığı: middle kapalıysa resize modu
                    if grab_now:
                        mid_open = _finger_distance(lms[9], lms[12],
                                                    _palm_size(lms)) > 0.30
                        mode = "move" if mid_open else "resize"

                        if not grab_active:
                            wid = _window_under_cursor()
                            if wid:
                                grab_active = True
                                grab_wid = wid
                                grab_mode = mode
                                grab_start_cursor = cursor_pt
                                grab_start_hand = (mx, my)
                                grab_start_geo = _window_geometry(wid)
                                grab_last_ynorm = my
                                grab_last_t = now
                                grab_begin_t = now
                                print("GRAB", mode, wid)
                                log_event(f"grab {mode} window {wid}")
                            else:
                                # Tutabilir pencere yok -> yine de imleç izler
                                pass
                        else:
                            # Aktif tutma: taşı / boyutlandır
                            ddx = (mx - grab_start_hand[0]) * SCREEN_W
                            ddy = (my - grab_start_hand[1]) * SCREEN_H
                            if grab_mode == "move" and grab_start_geo:
                                nx = grab_start_geo[0] + ddx
                                ny = grab_start_geo[1] + ddy
                                _wmctrl_geometry(grab_wid, nx, ny,
                                                 grab_start_geo[2],
                                                 grab_start_geo[3])
                            elif grab_mode == "resize" and grab_start_geo:
                                nw = int(max(120, grab_start_geo[2] + ddx))
                                nh = int(max(120, grab_start_geo[3] + ddy))
                                _wmctrl_geometry(grab_wid, grab_start_geo[0],
                                                 grab_start_geo[1], nw, nh)

                            # Aşağı sallama = kapat
                            if grab_last_ynorm is not None:
                                dtyn = my - grab_last_ynorm
                                dt = now - grab_last_t
                                if dt > 0.02 and (now - grab_begin_t) > 0.4 \
                                        and dtyn > 0.15:
                                    print("Shake-down detected -> close")
                                    log_event("close via shake-down")
                                    _close_window(grab_wid)
                                    last_action = ("Window closed", now)
                                    grab_active = False
                                    grab_wid = None
                            grab_last_ynorm = my
                            grab_last_t = now

                    else:
                        if grab_active:
                            print("RELEASE", grab_wid)
                            log_event("release grab")
                        grab_active = False
                        grab_wid = None

                    # Orta+başparmak = tıklama (grab değilken)
                    if not grab_now:
                        if click_now and not click_prev_pinch:
                            print("CLICK")
                            log_event("click")
                            _click("1")
                            last_action = ("Click", now)
                        click_prev_pinch = click_now

                    # --hands: parlaklık / ses pinçleri
                    if HANDS_MODE and len(result.multi_hand_landmarks or []) >= 1:
                        pinch_val = _finger_distance(lms[4], lms[8], _palm_size(lms))
                        pct = max(0.0, min(1.0, 1.0 - pinch_val / 0.9)) * 100.0
                        # El rolü: label "Left" -> ses, "Right" -> parlaklık
                        if hand_label == "Left":
                            if prev_l_pinch is None or now - last_apply > 0.15:
                                if prev_l_pinch is not None and \
                                        abs(pct - prev_l_pinch) > 3.0:
                                    set_volume_percent(pct)
                                    last_apply = now
                                    last_action = (f"Volume {pct:.0f}%", now)
                            prev_l_pinch = pct
                        else:
                            if prev_r_pinch is None or now - last_apply > 0.15:
                                if prev_r_pinch is not None and \
                                        abs(pct - prev_r_pinch) > 3.0:
                                    set_brightness_percent(pct)
                                    last_apply = now
                                    last_action = (f"Brightness {pct:.0f}%", now)
                            prev_r_pinch = pct
                    else:
                        prev_r_pinch = None
                        prev_l_pinch = None
            else:
                cursor_pt = None

            # Ekranda durum metni
            if last_action[0] and (now - last_action[1]) < 2.0:
                cv2.putText(frame, last_action[0], (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2)
            cv2.putText(frame, "q/ESC = quit | index=move, idx+thumb=move, "
                        "idx+thumb+middle close=resize, shake=close",
                        (10, frame.shape[0] - 15),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
            if HANDS_MODE:
                cv2.putText(frame, "Left pinch = volume, Right pinch = brightness",
                            (10, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                            (0, 200, 255), 1)

            cv2.imshow("WindowGesture", frame)
            k = cv2.waitKey(1) & 0xFF
            if k in (ord("q"), 27):
                break
            if cv2.getWindowProperty("WindowGesture", cv2.WND_PROP_VISIBLE) < 1:
                break
    finally:
        if landmarker is not None:
            landmarker.close()
        if cap is not None:
            cap.release()
        try:
            cv2.destroyAllWindows()
        except Exception:
            pass
        _release_single_lock()
        log_event("stopped (normal)")
        print("Stopped.")


HAND_CONNS = [
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20),
    (0, 17),
]

if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        _release_single_lock()
        raise
    except Exception as e:
        _release_single_lock()
        import traceback
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write("%s CRASH:\n%s\n" % (
                    time.strftime("%Y-%m-%d %H:%M:%S"), traceback.format_exc()))
        except Exception:
            pass
        traceback.print_exc()
        sys.exit(1)