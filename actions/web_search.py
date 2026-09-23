import webbrowser
from urllib.parse import quote_plus
from tts import edge_speak


def web_search(
    parameters: dict,
    player=None,
    session_memory=None,
):
    """
    Web search action: opens the query in Google in the browser and gives a
    short spoken confirmation. (No external search API key required.)
    """
    query = (parameters or {}).get("query", "").strip()

    if not query:
        msg = "Sir, I couldn't understand the search request."
        if player:
            player.write_log(f"GANDALF: {msg}")
        edge_speak(msg)
        return msg

    url = f"https://www.google.com/search?q={quote_plus(query)}"

    try:
        webbrowser.open(url)
    except Exception:
        msg = "Sir, I couldn't open the browser for that search."
        if player:
            player.write_log(f"GANDALF: {msg}")
        edge_speak(msg)
        return msg

    msg = f"Searching Google for {query}."
    if player:
        player.write_log(f"GANDALF: {msg}")
    edge_speak(msg)

    if session_memory:
        try:
            session_memory.set_last_search(query, msg)
        except Exception:
            pass

    return msg
