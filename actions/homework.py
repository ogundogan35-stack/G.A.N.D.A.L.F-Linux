# -*- coding: utf-8 -*-
"""
Ödev listesi (homework) modülü — Gandalf'ın Telegram/voice kanalına bağlı.

kullandığı JSON formatı Ömer'in kendi "odev.py" uygulamasıyla birebir aynı:
  [ { "ders", "sayfa", "tanim", "teslim", "durum" (bool) }, ... ]

Varsayılan dosya: <ProjectGandalf>/egitim/odev/odevler.json
(env override: HOMEWORK_JSON)
"""
import json
import os
import threading

_lock = threading.Lock()

_DEFAULT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))),
        "egitim", "odev", "odevler.json")
)

JSON_YOLU = os.environ.get("HOMEWORK_JSON", _DEFAULT)


def _yukle():
    try:
        with open(JSON_YOLU, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _kaydet(odevler):
    os.makedirs(os.path.dirname(JSON_YOLU), exist_ok=True)
    with open(JSON_YOLU, "w", encoding="utf-8") as f:
        json.dump(odevler, f, ensure_ascii=False, indent=4)


def _satir(i, o):
    durum = "[X]" if o.get("durum") else "[ ]"
    ders = o.get("ders", "") or ""
    sayfa = o.get("sayfa", "") or ""
    teslim = o.get("teslim", "") or ""
    tanim = o.get("tanim", "") or ""
    parcalar = []
    if ders:
        parcalar.append(ders)
    if sayfa:
        parcalar.append(f"pages {sayfa}")
    if teslim:
        parcalar.append(f"due {teslim}")
    if tanim:
        parcalar.append(tanim)
    aciklama = " | ".join(parcalar)
    return f"{i}) {durum} {aciklama}"


def _no_secim(no):
    try:
        idx = int(no) - 1
    except (TypeError, ValueError):
        return None
    if idx < 0:
        return None
    return idx


def homework_action(action="list", ders="", sayfa="", teslim="", tanim="",
                    no=None, alan="", deger=""):
    with _lock:
        odevler = _yukle()
        a = (action or "").strip().lower()

        if a in ("list", "oku", "read", "goster"):
            if not odevler:
                return "No homework has been added yet."
            satirlar = [f"Homework list ({len(odevler)} entries):"]
            for i, o in enumerate(odevler, start=1):
                satirlar.append(_satir(i, o))
            return "\n".join(satirlar)

        if a in ("add", "ekle", "kaydet", "save"):
            if not ders and not tanim:
                return "To add homework, at least a subject name or a description is required."
            odevler.append({
                "ders": ders.strip(),
                "sayfa": sayfa.strip(),
                "tanim": tanim.strip(),
                "teslim": teslim.strip(),
                "durum": False,
            })
            _kaydet(odevler)
            return (f"Homework saved: {ders.strip() or tanim.strip()}. "
                    f"List has {len(odevler)} entries.")

        if a in ("done", "tik", "tamamla", "bitir", "check"):
            if no is None:
                return "Tell me which homework to mark done (number)."
            idx = _no_secim(no)
            if idx is None or idx >= len(odevler):
                return "Invalid homework number."
            odevler[idx]["durum"] = True
            _kaydet(odevler)
            return f"Marked done: {odevler[idx].get('ders') or odevler[idx].get('tanim')}"

        if a in ("delete", "sil", "del"):
            if no is None:
                return "Which homework should I delete (number)?"
            idx = _no_secim(no)
            if idx is None or idx >= len(odevler):
                return "Invalid homework number."
            kaldirilan = odevler.pop(idx)
            _kaydet(odevler)
            return f"Deleted: {kaldirilan.get('ders') or kaldirilan.get('tanim')}"

        if a in ("update", "guncelle", "duzenle"):
            if no is None:
                return "Tell me which homework to update (number)."
            idx = _no_secim(no)
            if idx is None or idx >= len(odevler):
                return "Invalid homework number."
            alan_key = {"ders": "ders", "sayfa": "sayfa", "tanim": "tanim",
                        "teslim": "teslim"}.get((alan or "").strip().lower())
            if alan_key:
                odevler[idx][alan_key] = deger.strip()
            else:
                if ders:
                    odevler[idx]["ders"] = ders.strip()
                if sayfa:
                    odevler[idx]["sayfa"] = sayfa.strip()
                if teslim:
                    odevler[idx]["teslim"] = teslim.strip()
                if tanim:
                    odevler[idx]["tanim"] = tanim.strip()
            _kaydet(odevler)
            return "Homework updated: " + _satir(idx + 1, odevler[idx])

        return ("Unknown operation. Use: "
                "list | add | done:<no> | delete:<no> | update:<no>")