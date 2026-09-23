# -*- coding: utf-8 -*-
"""Screen vision module for Gandalf.

Adds Gemini vision capabilities:
  - describe_screen(question): take a screenshot and ask Gemini to describe /
    answer about what's visible on screen.
"""

import tempfile

from PIL import Image

import pyautogui
pyautogui.FAILSAFE = False  # corner-bound mouse must not abort automation


def _client():
    """Reuse the same Gemini client as gemini_handler (lazy import avoids
    cycles; _get_client is cached so one client is shared app-wide)."""
    import gemini_handler as gh
    return gh._get_client()


def screenshot_bytes(max_width: int = 1280) -> bytes:
    """Take a screenshot and return PNG bytes (downscaled to keep tokens low).

    Uses the shared fallback chain (pyautogui -> scrot -> gnome-screenshot ->
    ImageMagick import) so it keeps working under GNOME Wayland / headless
    setups where pyautogui cannot capture the screen.
    """
    import os
    import time
    from actions.system_diagnose import _take_screenshot
    out = _take_screenshot(os.path.join(
        tempfile.gettempdir(),
        f"gandalf_shot_{int(time.time() * 1000)}.png"))
    if isinstance(out, str) and not out.startswith("Screenshot failed"):
        shot = Image.open(out)
        if shot.width > max_width:
            ratio = max_width / shot.width
            shot = shot.resize((max_width, int(shot.height * ratio)),
                               Image.LANCZOS)
        buf = tempfile.SpooledTemporaryFile()
        shot.save(buf, format="PNG")
        buf.seek(0)
        return buf.read()
    raise RuntimeError("Screenshot failed: " + str(out))


def _ask_gemini(prompt: str, images, use_tools: bool = False) -> str:
    import gemini_handler as gh
    if not gh.GEMINI_OK:
        return ""
    client = _client()
    parts = []
    if isinstance(images, (list, tuple)):
        for img in images:
            parts.append(gh.types.Part.from_bytes(
                data=img, mime_type="image/png"))
    else:
        parts.append(gh.types.Part.from_bytes(
            data=images, mime_type="image/png"))
    contents = [
        gh.types.Content(role="user", parts=parts),
        gh.types.Content(role="user",
                         parts=[gh.types.Part.from_text(text=prompt)]),
    ]
    cfg = gh._config(use_tools=use_tools)
    resp, err = gh._generate(client, contents, cfg)
    if resp is None:
        first = err[0] if err else "unknown"
        return f"Gemini API is temporarily unavailable ({first[:80]})."
    try:
        return resp.text
    except Exception:
        return ""


def describe_screen(question: str = "") -> str:
    """Describe what's on the screen right now."""
    try:
        img = screenshot_bytes()
    except Exception as e:
        return f"Could not take a screenshot: {e}"
    prompt = (
        "You are Gandalf. Describe what is on the user's screen in a short, "
        "clear way. Mention the active window/apps, the visible content, "
        "anything notable. If the user asked a question, answer it."
    )
    if question:
        prompt += "\n\nThe user's question: " + question
    return _ask_gemini(prompt, img)