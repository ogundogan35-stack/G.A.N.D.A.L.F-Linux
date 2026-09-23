from tts import edge_speak
try:
    from actions.live_weather import get_weather_summary
except Exception:  # noqa: BLE001
    get_weather_summary = None


def weather_action(
    parameters: dict,
    player=None,
    session_memory=None
):
    """
    Weather report action.
    Fetches live weather from Open-Meteo and speaks a Turkish summary with
    go/No-go advice (sıcak/soğuk, yağmur var/yok, dışarı çıkılır/çıkılmaz).
    No browser tab is opened anymore — the answer is given directly.
    """

    city = parameters.get("city")
    time = parameters.get("time")
    if not city or not isinstance(city, str):
        msg = "Sir, the city is missing for the weather report."
        _speak_and_log(msg, player)
        return msg

    if not time or not isinstance(time, str):
        time = "today"
    else:
        time = time.strip()

    if get_weather_summary is None:
        msg = "Sir, the weather module could not be loaded."
        _speak_and_log(msg, player)
        return msg

    try:
        msg = get_weather_summary(city, when=time)
    except ValueError as e:
        msg = f"Sir, I could not find that city: {e}"
        _speak_and_log(msg, player)
        return msg
    except Exception as e:  # network/timeout etc.
        msg = f"Sir, I could not fetch the weather: {e}"
        _speak_and_log(msg, player)
        return msg

    _speak_and_log(msg, player)

    if session_memory:
        try:
            session_memory.set_last_search(
                f"weather in {city} {time}",
                msg
            )
        except Exception:
            pass

    return msg


def _speak_and_log(message: str, player=None):
    """Helper: log + TTS safely"""
    if player:
        try:
            player.write_log(f"GANDALF: {message}")
        except Exception:
            pass

    try:
        edge_speak(message)
    except Exception:
        pass
