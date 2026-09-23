# GANDALF — Linux Port

Bu klasör, `G.A.N.D.A.L.F` (Windows) projesinin **Linux sürümüdür**. Orijinal
Windows projesi değiştirilmedi; tüm değişiklikler yalnızca bu kopyada.

## Kurulum (tek sefer)

```bash
cd ~/.../G.A.N.D.A.L.F\ \(Linux\)
bash setup.sh        # apt paketleri + .venv + pip install (sudo ister)
```

`setup.sh` şunları kurar:

- Runtime: `python3-venv`, `python3-pip`, **`python3-tk`** (UI için zorunlu),
  `portaudio19-dev`/`libportaudio2`/`libsndfile1` (ses), **`libgl1`** +
  `libglib2.0-0` + `libgtk2.0-0` + `libgtk-3-bin` (OpenCV/MediaPipe için zorunlu)
- Kontrol: `xdotool`, `wmctrl`, `brightnessctl`, `pulseaudio-utils`,
  `alsa-utils`, `x11-utils`, `x11-xserver-utils` (xset), `dbus-x11`
- OCR/seslendirme: `tesseract-ocr` + **`tesseract-ocr-tur`** (Türkçe OCR),
  `espeak-ng`, `speech-dispatcher`
- Pano/ekran görüntüsü: `xclip`, `xsel`, `wl-clipboard`, `scrot`,
  `libgtk-3-bin` (gtk-launch)

Python paketleri proje içindeki `.venv`'e kurulur (sisteminize bulaşmaz).

## Çalıştırma

```bash
bash run.sh
# veya kaynağı açın:  source .venv/bin/activate && python main.py
```

İsteğe bağlı güvenlik taraması motoru (heuristik tarama zaten çalışır):

```bash
sudo apt install clamav clamav-daemon
sudo freshclam   # imza güncelle
```

## Windows → Linux değişiklik özeti

| Bileşen | Windows | Linux sürümü |
|---|---|---|
| Giriş dosyası | `main.pyw` | `main.py` (`py main.pyw` -> `bash run.sh`) |
| TTS | PowerShell/SAPI | `espeak-ng` / `espeak` / `spd-say` / `pyttsx3` (hepsi yoksa sessiz) |
| Pencere simgesi | `iconbitmap(.ico)` | PIL ile PNG + `iconphoto` |
| Uygulama açma | Başlat menüsü araması (`win` tuşu) | `shutil.which` / `.desktop` eşleştirme / `xdg-open` (site isimleri) |
| Ses | Windows CoreAudio / tuş basımı | `pactl` (fallback `amixer`) |
| Parlaklık | WMI | `brightnessctl` (fallback `xrandr --brightness`) |
| Kilit | `LockWorkStation` | `loginctl lock-session` / `xdg-screensaver` / `dm-tool` |
| Uyku/kapanma | `rundll32` / `shutdown.exe` | `systemctl suspend/hibernate` / `shutdown -P/+1` |
| Wi-Fi | `netsh` | `nmcli radio wifi on/off` |
| Duvar kâğıdı | `SystemParametersInfoW` | `xfconf-query` (XFCE/Pardus) / `gsettings` (GNOME, Cinnamon) / `plasma-apply-wallpaperimage` (KDE) |
| Pencere yönetimi | ctypes WinAPI | `wmctrl` + `xdotool` |
| Pano | Win32 clipboard | `wl-paste`/`wl-copy` (Wayland) veya `xclip`/`xsel` (X11) |
| Ekran aç/kapa | `SC_MONITORPOWER` | `xset dpms force off/on` (X11) |
| Dosya açma | `os.startfile` | `xdg-open` / `gio open` |
| Güvenlik taraması | Windows Defender | `clamscan` (kuruluysa) + heuristik |
| Güvenli silme | Geri Dönüşüm Kutusu (`SHFileOperation`) | FreeDesktop `~/.local/share/Trash` |
| El kontrolü | GDI overlay + WinAPI | yeni Linux `window_gesture.py` (mediapipe + xdotool/wmctrl/brightnessctl/pactl; iskelet webcam önizleme penceresinde çizilir) |
| Başlatıcı | `.bat` | `.sh` (`baslat.sh`) |

## Platform bağımlı paketler

`requirements.txt` içinde `pycaw` ve `screen-brightness-control` artık yalnızca
Windows'ta kurulur (`; sys_platform == 'win32'`), `python-xlib` ise yalnızca
Linux'ta kurulur (`; sys_platform != 'win32'`) — pyautogui'nin fare/tuş kontrolü
için ihtiyacı vardır. Gerisi (ses/parlaklık/ekran) Linux'ta sistem araçlarıyla
yapılır, ek Python paketi gerekmez.

## Desteklenen dağıtımlar ve masaüstü ortamları

Proje **apt tabanlı her dağıtımda** çalışır: Pardus, Parrot OS, Linux Mint,
Ubuntu, Debian. Masaüstü ortamına göre davranış:

| Masaüstü | Pencere yönetimi / otomasyon / ekran aç-kapa | Duvar kâğıdı |
|---|---|---|
| **XFCE** (Pardus varsayılanı) | Tam destek (X11) | `xfconf-query` |
| GNOME / Cinnamon | Xorg oturumunda tam | `gsettings` |
| KDE | Xorg oturumunda tam | `plasma-apply-wallpaperimage` |
| Wayland (herhangi bir DE) | Kısıtlı — pyautogui/wmctrl/xdotool/xset büyük ölçüde devre dışı | DE'ye göre kısmen |

> **Öneri:** Xorg (X11) oturumunda oturum açın (giriş ekranında "Xorg / X11"
> seçin). GNOME'un varsayılan olduğu yerlerde "GNOME on Xorg" bulunur; Pardus
> (XFCE) doğrudan X11'dir ve en yüksek uyumluluğu verir.

## Wayland'de çalışmayanları çalışır hale getirmek

Wayland, pencereleri ve girişi uygulamanın arkasına sakladığı için `xset`,
`wmctrl`, `xdotool`, `pyautogui`, `scrot` gibi X11 araçlarını kısıtlar. Yöntemler:

- **En kolayı:** Xorg oturumuna geçin — tüm özellikler anında çalışır.
- **Ekran görüntüsü (GNOME Wayland):** zaten çalışır — Gandalf artık
  `gnome-screenshot -f` fallback'ini kullanıyor (snapshot yenilenmezse
  `gnome-screenshot` kurulu olduğundan emin olun).
- **Fare/klavye otomasyonu (Wayland):** `ydotool` + `ydotool.service` kurun
  (kullanıcıyı `input` grubuna ekleyin); `xdg-desktop-portal` veya `wtype`
  (klavye) alternatifleridir. Not: Gandalf'a özgü değil, global otomasyon
  aracıdır.
- **Pencere yönetimi (KDE Wayland):** `kdotool` D-Bus üzerinden çalışır.
  GNOME Wayland'de pencere kontrolü, sistem uzantısı olmadan kapalıdır.
- **Ekran aç/kapa (Wayland):** Xorg gerekir (GNOME'daki
  `gnome-screensaver-command -l` yine de ekranı kilitler).

## Önemli notlar

- **El kontrolü**: `window_gesture.py` X11 oturumlarında çalışır
  (Wayland'de xdotool'ün kısıtları vardır). `--hands` modunda sağ el pinç
  parlaklığı, sol el pinç sesi ayarlar; işaret parmağı imleci taşır, orta+
  başparmak tıklar, işaret+başparmak pencereleri taşır/boyutlandırır, aşağı
  sallama pencereyi kapatır. XFCE panelinde tepsi simgesinin görünmesi için
  ekranınızın "Notification Area / Tray" bölümünde `window_gesture.py`'yi
  etkinleştirmeniz gerekebilir (simge yoksa bile `q`/ESC ile kapanır).
- **Oturum türü**: pencere yönetimi ve ekran aç/kapa X11 gerektirir; pek çok
  GNOME/KDE kurulumu Xorg oturumunda da çalışır. Wayland'de bu özellikler zarif
  şekilde devre dışı kalır (hata fırlatmaz).
- Vosk modeli ilk çalıştırmada otomatik iner; cache yolu:
  `~/.cache/vosk/vosk-model-small-en-us-0.15`.
- `.env` dosyası orijinalden kopyalanmıştır (hedef makinede anahtarları
  kendinize göre güncelleyin).
- Ödevler (`homework.py`): varsayılan JSON yolu bu projede
  `<proje>/egitim/odev/odevler.json` (Windows'taki Desktop yolu aksine);
  isterseniz `.env`'e `HOMEWORK_JSON=/yol/odevler.json` ekleyerek ezebilirsiniz.
- Türkçe OCR için `tesseract-ocr-tur` setup'ta kurulur (Pardus dahil Debian
  repolarında mevcuttur).
- `Gandalf.spec` düzeltildi: `actions.aircraft_report` (mevcut olmayan modül)
  kaldırıldı, eksik `actions.*` modülleri hiddenimport'a eklendi.