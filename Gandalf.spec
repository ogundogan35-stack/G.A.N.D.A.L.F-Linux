# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all
import os

# Data files - using relative paths for portability
datas = [
    ('core/prompt.txt', 'core'),
    ('templates', 'templates'),
    # NOTE: .env is intentionally NOT bundled — secrets are never committed.
    # Copy ".env.example" to ".env" on the target machine instead.
    ('commands.txt', '.'),
    ('memory', 'memory'),
    ('gandalf.ico', '.'),
    ('WindowGesture/window_gesture.py', 'WindowGesture'),
    ('WindowGesture/hand_landmarker.task', 'WindowGesture'),
]

# Collect all dependencies for portability
binaries = []
hiddenimports = [
    'tts',
    'dashboard_server',
    'speech_to_text',
    'llm',
    'gemini_handler',
    'ui',
    'runtime_state',
    'screen_utils',
    'actions.open_app',
    'actions.web_search',
    'actions.weather_report',
    'actions.system_control',
    'actions.spotify',
    'actions.hand_control',
    'actions.file_ops',
    'actions.live_weather',
    'actions.homework',
    'actions.computer',
    'actions.clipboard_screen',
    'actions.screen_vision',
    'actions.window_control',
    'actions.system_diagnose',
    'memory.memory_manager',
    'memory.temporary_memory',
    'telegram_bot',
]

# Collect vosk dependencies
tmp_ret = collect_all('vosk')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# Collect other dependencies
for module in ['customtkinter', 'sounddevice', 'numpy', 'requests', 
               'python_dotenv', 'flask', 'psutil', 'pyautogui', 'pillow',
               'google.genai', 'telegram', 'cv2', 'mediapipe', 'pystray']:
    try:
        tmp_ret = collect_all(module)
        datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
    except:
        pass

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Gandalf',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='gandalf.ico',
)
