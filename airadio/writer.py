"""Hírblokk-szöveg írása Claude-dal, kötött JSON-sémában.

A modell csak a megadott hírekből dolgozhat, és minden hírnél meg kell
neveznie, melyik bemeneti tételből (source_id) írta. A news modul eldobja
azt a tételt, amelynek azonosítója nem létezik.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

import anthropic
from pydantic import BaseModel, ValidationError

from .feeds import NewsItem

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"

BULLETIN_SCHEMA = {
    "type": "object",
    "properties": {
        "intro": {"type": "string"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_id": {"type": "integer"},
                    "text": {"type": "string"},
                },
                "required": ["source_id", "text"],
                "additionalProperties": False,
            },
        },
        "outro": {"type": "string"},
    },
    "required": ["intro", "items", "outro"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """\
Egy magyar nyelvű, személyes internetes rádió hírszerkesztője vagy. Rövid, \
felolvasásra szánt hírblokkot írsz, amelyet egy szövegfelolvasó (TTS) hang mond el.

Tartalmi szabályok:
- Kizárólag a megadott hírtételekben szereplő tényeket használhatod. Ne egészítsd \
ki saját tudásból, ne találgass, ne értékelj, ne kommentálj.
- Minden hírnél nevezd meg a forrást természetes módon, például: "A Telex szerint…".
- Hagyd ki azt a hírt, amely bizonytalan, megerősítetlen vagy pletyka jellegű, \
amelyből csak egy cím van tartalom nélkül, amely reklám vagy szponzorált tartalom, \
illetve amely kényes: öngyilkosság részletei, áldozatok vagy kiskorúak azonosítása, \
erőszak részletes leírása. Inkább legyen rövidebb a blokk.
- A nemrég elhangzott híreket ne ismételd meg, hacsak nincs bennük új fejlemény.
- Fontossági sorrendben haladj; egy-egy hír két-négy mondat.

Felolvashatóság:
- Folyó szöveg, rövid mondatok. Ne használj felsorolást, zárójelet, idézőjelet, \
emojit, webcímet vagy rövidítést.
- A számokat, dátumokat, mértékegységeket és jeleket betűvel írd ki \
(például "huszonhárom százalék", "három és fél milliárd forint").

Szerkezet:
- intro: a megadott műsorvezetői karakter hangján egy-két mondat, amely bemondja \
az időt és felvezeti a híreket.
- items: a kiválasztott hírek, mindegyiknél a bemeneti tétel azonosítójával (source_id).
- outro: egyetlen rövid záró mondat ugyanattól a műsorvezetőtől.
"""

HUNGARIAN_WEEKDAYS = ("hétfő", "kedd", "szerda", "csütörtök", "péntek", "szombat", "vasárnap")


class BulletinItem(BaseModel):
    source_id: int
    text: str


class Bulletin(BaseModel):
    intro: str
    items: list[BulletinItem]
    outro: str


class WriterError(Exception):
    pass


def build_prompt(
    items: list[NewsItem],
    slot: datetime,
    host: str,
    recent_titles: list[str],
    max_items: int,
) -> str:
    listing = [
        {
            "id": i,
            "forras": item.source,
            "cim": item.title,
            "osszefoglalo": item.summary,
            "megjelent": item.published.astimezone().strftime("%Y-%m-%d %H:%M"),
        }
        for i, item in enumerate(items, start=1)
    ]
    weekday = HUNGARIAN_WEEKDAYS[slot.weekday()]
    clock = f"{slot.hour} óra {slot.minute} perc" if slot.minute else f"{slot.hour} óra"
    parts = [
        f"Adásidő: {slot:%Y-%m-%d}, {weekday}, {clock}.",
        f"Műsorvezetői karakter: {host or 'tárgyilagos hírolvasó'}",
        f"Legfeljebb {max_items} hírt válassz.",
        "Nemrég elhangzott hírek címei:",
        json.dumps(recent_titles, ensure_ascii=False) if recent_titles else "(nincs)",
        "Hírtételek:",
        json.dumps(listing, ensure_ascii=False, indent=1),
    ]
    return "\n\n".join(parts)


def write_bulletin(
    client: anthropic.Anthropic,
    *,
    model: str,
    effort: str,
    fallback: bool,
    items: list[NewsItem],
    slot: datetime,
    host: str,
    recent_titles: list[str],
    max_items: int,
) -> Bulletin:
    extra = {"betas": [FALLBACK_BETA], "fallbacks": "default"} if fallback else {}
    try:
        response = client.beta.messages.create(
            model=model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_prompt(items, slot, host, recent_titles, max_items)}],
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": BULLETIN_SCHEMA},
            },
            **extra,
        )
    except anthropic.APIConnectionError as e:
        raise WriterError(f"Claude API nem érhető el: {e}") from e
    except anthropic.APIStatusError as e:
        raise WriterError(f"Claude API hiba ({e.status_code}): {e.message}") from e

    if response.stop_reason == "refusal":
        details = getattr(response, "stop_details", None)
        category = getattr(details, "category", None)
        raise WriterError(f"A modell elutasította a kérést (kategória: {category})")
    if response.stop_reason == "max_tokens":
        raise WriterError("A válasz elérte a max_tokens korlátot, csonka")

    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise WriterError(f"Nincs szöveges válasz (stop_reason: {response.stop_reason})")
    try:
        bulletin = Bulletin.model_validate_json(text)
    except ValidationError as e:
        raise WriterError(f"A válasz nem felel meg a sémának: {e}") from e

    usage = response.usage
    log.info(
        "Claude (%s): %s bemeneti, %s kimeneti token",
        response.model,
        usage.input_tokens,
        usage.output_tokens,
    )
    return bulletin
