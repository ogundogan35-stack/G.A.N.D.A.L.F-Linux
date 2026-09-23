"""Spotify Web API control for Gandalf (playlists / tracks / playback).

Lets Gandalf search Spotify and play a specific playlist, album or track on the
user's active Spotify device, plus basic playback controls (pause / next /
volume / shuffle / repeat).

Requires (set up once):
    .env:
        SPOTIFY_CLIENT_ID     - from the Spotify Developer Dashboard
        SPOTIFY_CLIENT_SECRET - from the Spotify Developer Dashboard
        SPOTIFY_REDIRECT_URI  - http://127.0.0.1:8888/callback
    memory/spotify_token.json - access + refresh tokens (from the OAuth step)

The token file has a 1-hour access token and a 6-month refresh token. The module
auto-refreshes the access token; if the refresh token also expired (invalid_grant),
tells the user to re-authorize.
"""

import os
import json
import time
import threading

import requests

from dotenv import load_dotenv

# Allow-safe import of the UI (player) helper style used by other actions.
# session_memory / player (ui) are optional.

_load_done = False
_key_lock = threading.Lock()
_token_cache = None


def _load_env_once():
    global _load_done
    if _load_done:
        return
    try:
        load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
    except Exception:
        pass
    _load_done = True


def _token_path():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "memory", "spotify_token.json")


def _read_tokens():
    global _token_cache
    path = _token_path()
    if not os.path.isfile(path):
        return None
    with _key_lock:
        try:
            with open(path, "r", encoding="utf-8") as f:
                _token_cache = json.load(f)
        except Exception:
            return None
    return _token_cache


def _save_tokens(tokens):
    with _key_lock:
        try:
            with open(_token_path(), "w", encoding="utf-8") as f:
                json.dump(tokens, f, indent=2)
        except Exception:
            pass


def _refresh():
    """Exchange the stored refresh token for a fresh access token."""
    toks = _read_tokens()
    if toks is None or not toks.get("refresh_token"):
        return None
    client_id = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        # No real Spotify app credentials configured (see .env.example).
        return None
    try:
        r = requests.post(
            "https://accounts.spotify.com/api/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": toks["refresh_token"],
            },
            headers={
                "Authorization": "Basic " + _b64(f"{client_id}:{client_secret}"),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=30,
        )
        if r.status_code != 200:
            # 400 invalid_grant => refresh token expired / revoked.
            return None
        data = r.json()
        new_tok = data.get("access_token")
        if not new_tok:
            return None
        toks["access_token"] = new_tok
        toks["expires_at"] = time.time() + int(data.get("expires_in", 3600))
        if data.get("refresh_token"):
            toks["refresh_token"] = data["refresh_token"]
        _save_tokens(toks)
        return new_tok
    except Exception:
        return None


def _b64(s: str) -> str:
    import base64
    return base64.b64encode(s.encode("utf-8")).decode("utf-8")


def _get_access_token():
    """Return a valid access token, refreshing first if needed, or None."""
    _load_env_once()
    toks = _read_tokens()
    if toks is None or not toks.get("access_token"):
        return None
    try:
        if time.time() < float(toks.get("expires_at", 0)):
            return toks["access_token"]
    except Exception:
        pass
    return _refresh()


def _api(method, path, token, params=None, body=None):
    url = "https://api.spotify.com/v1" + path
    headers = {"Authorization": "Bearer " + token}
    try:
        r = requests.request(method, url, headers=headers,
                             params=params, json=body, timeout=30)
    except Exception as e:
        return {"_error": f"network: {e}"}
    if r.status_code in (200, 201, 204):
        if r.status_code == 204 or not (r.text or "").strip():
            return {}
        try:
            return r.json()
        except Exception:
            return {}
    # 401 => token probably stale; one refresh retry handled by caller.
    return {"_error": f"spotify {r.status_code}: {r.text[:200]}"}


def _active_device(token):
    data = _api("GET", "/me/player/devices", token)
    if "_error" in data:
        return None, data.get("_error")
    devices = data.get("devices") or []
    active = [d for d in devices if d.get("is_active")]
    if active:
        return active[0].get("id"), None
    online = [d for d in devices if d.get("is_restricted") is not True and d.get("name")]
    if online:
        return online[0].get("id"), None
    return None, "spotify_open"


def search(token, query, type_filter="track,playlist,album,artist", limit=8):
    data = _api("GET", "/search", token,
                params={"q": query, "type": type_filter, "limit": limit,
                        "market": "TR"})
    if "_error" in data:
        return None, data.get("_error")
    return data, None


def _report(player, msg):
    try:
        player.write_log(f"GANDALF: {msg}")
    except Exception:
        pass
    return msg


def _play(token, uri=None, uris=None, device_id=None, position_ms=0):
    body = {}
    if uri:
        body["context_uri"] = uri
    elif uris:
        body["uris"] = uris
    if position_ms:
        body["position_ms"] = position_ms
    path = "/me/player/play" + (f"?device_id={device_id}" if device_id else "")
    data = _api("PUT", path, token, body=body)
    return data


def _pick_best(results, prefer_type=None, query=""):
    """Pick the most relevant item from a search response.

    Default priority is TRACK first (users most often name a song), but a
    'playlist' / 'album' / 'artist' keyword in the query or a type hint moves
    that kind to the front. On top of ordering we do an exact (case-insensitive)
    name match against the whole query, and match the track name against any
    query word, so 'tool jimmy' selects Tool's track "Jimmy" instead of a random
    Tool playlist/song.
    """
    import re as _re
    q = (query or "").strip().lower()
    qwords = [w for w in _re.split(r"\W+", q) if len(w) > 1]

    def items(typ):
        # Spotify search sometimes returns null entries in the items list.
        return [x for x in ((results.get(typ + "s") or {}).get("items") or []) if x]

    def name_eq(item, word):
        return str((item.get("name") or "")).strip().lower() == word

    # Kind keywords in the query ("çalma listesi", "albüm", "sanatçı"...)
    # override the default track-first ordering.
    intent = prefer_type
    if not intent or intent == "auto":
        slug = " " + q.replace("ç", "c").replace("ğ", "g") \
                 .replace("ı", "i").replace("ö", "o").replace("ş", "s") \
                 .replace("ü", "u") + " "
        for kw, typ in (
                ("playlist", "playlist"), ("calma listes", "playlist"),
                ("listem", "playlist"), ("listesi", "playlist"),
                ("album", "album"),
                ("sanatci", "artist"), ("artist", "artist")):
            if kw in slug:
                intent = typ
                break

    base_order = ["track", "album", "playlist", "artist"]
    order = base_order
    if intent and intent in base_order:
        order = [intent] + [x for x in base_order if x != intent]

    # 1) Exact name match against the whole query (in preferred order).
    for typ in order:
        for it in items(typ):
            if name_eq(it, q):
                return it, typ

    # 2) Track whose name equals ANY query word (e.g. 'tool jimmy' -> Jimmy).
    for it in items("track"):
        if any(name_eq(it, w) for w in qwords):
            return it, "track"

    # 3) Fallback: first result of the computed order.
    for typ in order:
        its = items(typ)
        if its:
            return its[0], typ
    return None, None


def spotify_action(parameters, player=None, session_memory=None) -> str:
    """Dispatch a Spotify command.

    parameters:
        action: play | search_and_play | pause | next | previous | volume |
                shuffle | repeat
        query:   what to play / search (for play / search_and_play)
        type:    playlist | album | artist | track | "" (auto)  [optional hint]
        volume:  0-100 (for volume)
        shuffle: true/false (for shuffle)
        repeat:  off | context | track (for repeat)
    """
    params = parameters or {}
    action = str(params.get("action", "")).strip().lower()
    if not action:
        return _report(player, "Sir, which Spotify action should I take?")

    token = _get_access_token()
    if token is None:
        if not os.path.isfile(_token_path()):
            return _report(player,
                "Sir, Spotify is not linked yet. I need the OAuth setup first.")
        return _report(player,
            "Sir, my Spotify session has expired. I need to re-authorize once.")

    try:
        if action in ("play", "search_and_play"):
            return _handle_play(token, params, player)
        if action == "pause":
            data = _api("PUT", "/me/player/pause", token)
            return _report(player, "Spotify paused, sir." if "_error" not in data
                           else _spot_err(data["_error"]))
        if action == "next":
            _api("POST", "/me/player/next", token)
            return _report(player, "Skipping to the next track, sir.")
        if action == "previous":
            _api("POST", "/me/player/previous", token)
            return _report(player, "Going back a track, sir.")
        if action == "volume":
            try:
                vol = max(0, min(100, int(params.get("volume", 50))))
            except Exception:
                vol = 50
            _api("PUT", f"/me/player/volume?volume_percent={vol}", token)
            return _report(player, f"Spotify volume set to {vol} percent.")
        if action == "shuffle":
            st = "true" if str(params.get("shuffle", "")).lower() in ("true", "1", "yes", "karıştır") else "false"
            _api("PUT", f"/me/player/shuffle?state={st}", token)
            return _report(player, f"Shuffle {'on' if st == 'true' else 'off'}, sir.")
        if action == "repeat":
            mode = str(params.get("repeat", "off")).lower()
            if mode not in ("off", "context", "track"):
                mode = "off"
            _api("PUT", f"/me/player/repeat?state={mode}", token)
            return _report(player, f"Repeat set to {mode}, sir.")
        return _report(player, f"Sir, I don't support that Spotify action: {action}")
    except Exception as e:
        return _report(player, f"Sir, the Spotify action failed. ({e})")


def _handle_play(token, params, player):
    query = str(params.get("query", "")).strip()
    prefer = str(params.get("type", "")).strip().lower() or None

    if not query:
        # Just resume current playback on the active device.
        data = _play(token)
        if "_error" in data:
            return _report(player, _spot_err(data["_error"]))
        return _report(player, "Resuming Spotify, sir.")

    results, err = search(token, query)
    if err:
        return _report(player, _spot_err(err))
    item, typ = _pick_best(results, prefer, query)
    if item is None:
        return _report(player, f"Sir, I couldn't find '{query}' on Spotify.")

    uri = (item or {}).get("uri")
    name = (item or {}).get("name") or query
    if not uri:
        return _report(player, f"Sir, I couldn't find '{query}' on Spotify.")

    device_id, dev_err = _active_device(token)
    if dev_err:
        return _report(player, _spot_err(dev_err))
    if typ == "track":
        # A track cannot be a context_uri; queue exactly this song.
        data = _play(token, uris=[uri], device_id=device_id, position_ms=0)
    else:
        data = _play(token, uri=uri, device_id=device_id)
    if "_error" in data and data["_error"].startswith("spotify 403"):
        # Try again without forcing a device (use currently active).
        if typ == "track":
            data = _play(token, uris=[uri])
        else:
            data = _play(token, uri=uri)
    if "_error" in data:
        return _report(player, _spot_err(data["_error"]))

    label = {"playlist": "playlist", "album": "album", "artist": "artist",
             "track": "track"}.get(typ, "item")
    return _report(player, f"Playing the {label} '{name}' on Spotify, sir.")


def _spot_err(e):
    if not e:
        return "Sir, I couldn't reach Spotify."
    if "spotify_open" in str(e) or "NO_ACTIVE_DEVICE" in str(e).upper():
        return ("Sir, Spotify is not playing on any device. Please open the "
                "Spotify app on your computer or phone first.")
    if "403" in str(e):
        return "Sir, Spotify gave me permission trouble (check scopes or Premium)."
    return f"Sir, Spotify said: {e}"


# ---------------------------------------------------------------------------
# Convenience helpers used by the Telegram (gemini) tool path
# ---------------------------------------------------------------------------
def spotify_play(query: str, type_hint: str = "") -> str:
    """Tool-friendly one-call: play the best match for query on Spotify."""
    return spotify_action({"action": "search_and_play", "query": query,
                           "type": type_hint})


def spotify_pause() -> str:
    return spotify_action({"action": "pause"})


def spotify_skip(next_prev: str = "next") -> str:
    return spotify_action({"action": "next" if next_prev == "next" else "previous"})
