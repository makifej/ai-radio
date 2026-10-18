"""A webes felületeken tilos az emoji: csak SVG-ikonok lehetnek."""

from pathlib import Path

import pytest

PAGES = ["airadio/web/dashboard.html", "airadio/web/viz.js", "radio/player.html"]
# tipográfia megengedett: nagykötőjel, hármaspont, magyar idézőjelek
ALLOWED = set("–…„”")


@pytest.mark.parametrize("path", PAGES)
def test_no_emoji_or_symbol_glyphs(path):
    text = Path(path).read_text(encoding="utf-8")
    bad = sorted({c for c in text if ord(c) > 0x2000 and c not in ALLOWED})
    assert not bad, f"{path}: tiltott karakterek {[f'U+{ord(c):04X}' for c in bad]}"


def test_every_used_icon_is_defined():
    import re

    html = Path("airadio/web/dashboard.html").read_text(encoding="utf-8")
    defined = set(re.findall(r'<symbol id="i-([a-z]+)"', html))
    used = set(re.findall(r'ic\("([a-z]+)"', html)) | set(re.findall(r'href="#i-([a-z]+)"', html))
    assert used <= defined, f"hiányzó ikonok: {used - defined}"
