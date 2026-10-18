from datetime import datetime

import pytest

from airadio import writer
from conftest import fake_client, news_item

SLOT = datetime(2026, 10, 18, 8)


def call(client, fallback=True):
    return writer.write_bulletin(
        client,
        model="claude-sonnet-5-5",
        effort="medium",
        fallback=fallback,
        items=[news_item(1), news_item(2)],
        slot=SLOT,
        host="Reggeli házigazda",
        recent_titles=["Tegnapi hír"],
        max_items=4,
    )


def test_request_shape_and_parsing():
    payload = {"intro": "Nyolc óra van.", "items": [{"source_id": 1, "text": "A Teszt szerint…"}], "outro": "Ennyi."}
    client, messages = fake_client(payload)
    bulletin = call(client)

    assert bulletin.items[0].source_id == 1
    sent = messages.calls[0]
    assert sent["model"] == "claude-sonnet-5-5"
    assert sent["output_config"]["effort"] == "medium"
    assert sent["output_config"]["format"]["schema"] == writer.BULLETIN_SCHEMA
    assert sent["fallbacks"] == "default"
    assert sent["betas"] == [writer.FALLBACK_BETA]
    prompt = sent["messages"][0]["content"]
    assert "vasárnap, 8 óra" in prompt and "Reggeli házigazda" in prompt and "Tegnapi hír" in prompt


def test_fallback_can_be_disabled():
    client, messages = fake_client({"intro": "a", "items": [], "outro": "b"})
    call(client, fallback=False)
    assert "fallbacks" not in messages.calls[0] and "betas" not in messages.calls[0]


def test_refusal_raises():
    client, _ = fake_client("", stop_reason="refusal")
    with pytest.raises(writer.WriterError, match="general_harms"):
        call(client)


def test_schema_violation_raises():
    client, _ = fake_client({"intro": "a", "items": [{"text": "nincs azonosító"}], "outro": "b"})
    with pytest.raises(writer.WriterError, match="sémának"):
        call(client)
