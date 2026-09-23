# memory/memory_manager.py
import json
import os
from threading import Lock

# Mutlak yol: proje kökündeki memory/memory.json (CWD'ye bağlı değil, böylece
# Gandalf hangi klasörden başlatılırsa başlatılsın doğru dosya kullanılır).
MEMORY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory.json")
_lock = Lock()


def _empty_memory() -> dict:
    """Return an empty memory structure."""
    return {
        "identity": {},
        "preferences": {},
        "relationships": {},
        "emotional_state": {},
        "app_aliases": {},  # Store app name mappings like "Minecraft": "Tlauncher"
        "corrections": {},   # "what user said" -> "what it should mean" (learned)
        "topics": {}         # lowercase topic -> {"value": note} for AI priming
    }


def load_memory() -> dict:
    """Load memory from disk, return empty if not exists or invalid."""
    if not os.path.exists(MEMORY_PATH):
        return _empty_memory()

    with _lock:
        try:
            with open(MEMORY_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
                return _empty_memory()
        except Exception:
            return _empty_memory()


def save_memory(memory: dict) -> None:
    """Save memory to disk safely."""
    if not isinstance(memory, dict):
        return

    os.makedirs(os.path.dirname(MEMORY_PATH), exist_ok=True)

    with _lock:
        with open(MEMORY_PATH, "w", encoding="utf-8") as f:
            json.dump(memory, f, indent=2, ensure_ascii=False)


def _recursive_update(target: dict, updates: dict) -> bool:
    """Recursively merge updates into target memory. Returns True if changed."""
    changed = False

    for key, value in updates.items():
        if value is None or (isinstance(value, str) and not value.strip()):
            continue

        if isinstance(value, dict) and "value" not in value:
            if key not in target or not isinstance(target[key], dict):
                target[key] = {}
                changed = True
            if _recursive_update(target[key], value):
                changed = True
        else:

            entry = value if isinstance(value, dict) and "value" in value else {"value": value}
            if key not in target or target[key] != entry:
                target[key] = entry
                changed = True

    return changed


def update_memory(memory_update: dict) -> dict:
    """Merge LLM memory update into global memory and save."""
    if not isinstance(memory_update, dict):
        return load_memory()

    memory = load_memory()
    if _recursive_update(memory, memory_update):
        save_memory(memory)

    return memory


def set_app_alias(alias: str, real_app: str) -> dict:
    """Store an app alias mapping (e.g., "Minecraft" -> "Tlauncher")."""
    memory = load_memory()

    if "app_aliases" not in memory:
        memory["app_aliases"] = {}

    if memory["app_aliases"].get(alias) != real_app:
        memory["app_aliases"][alias] = real_app
        save_memory(memory)

    return memory


def get_app_alias(alias: str) -> str | None:
    """Get the real app name for an alias."""
    memory = load_memory()
    return memory.get("app_aliases", {}).get(alias)


def get_corrections() -> dict:
    """Return learned corrections: {what_user_said: what_it_should_mean}."""
    return load_memory().get("corrections", {}) or {}  # noqa: PLR5501


def get_topics() -> dict:
    """Return topic notes for AI priming: {lowercase_topic: {"value": note}}."""
    return load_memory().get("topics", {}) or {}  # noqa: PLR5501
