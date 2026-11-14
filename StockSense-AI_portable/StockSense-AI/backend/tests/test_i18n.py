"""Language detection and fixed-message coverage (no LLM)."""
import pytest

from app import i18n
from app.nl2sql import dates
import datetime as dt


@pytest.mark.parametrize("text,lang", [
    ("అక్టోబర్ 2026 లో మొత్తం ఆదాయం ఎంత?", "te"),
    ("Apples 1kg ఎంత అమ్మకాలు జరిగాయి", "te"),  # Telugu with an English product name
    ("अक्टूबर में राजस्व के हिसाब से टॉप 5 प्रोडक्ट्स दिखाओ", "hi"),
    ("Show me the top 5 products by revenue", "en"),
    ("october ka revenue kitna hai", "en"),      # Latin-script Hinglish is treated as English on purpose
    ("", "en"),
])
def test_detect_language(text, lang):
    assert i18n.detect_language(text) == lang


def test_every_message_exists_in_all_languages():
    for key, variants in i18n.M.items():
        assert set(variants) == {"en", "hi", "te"}, key
        assert all(v.strip() for v in variants.values()), key


def test_localised_messages_use_the_right_script_and_format():
    assert i18n.has_script(i18n.t("not_found", "te"), "te") and i18n.has_script(i18n.t("not_found", "hi"), "hi")
    assert "5" in i18n.t("found_n", "hi", n=5, more="") and "5" in i18n.t("found_n", "te", n=5, more="")
    assert i18n.t("not_found", "fr") == i18n.t("not_found", "en")  # unsupported language falls back to English


def test_month_guard_works_on_english_translation_not_native_script():
    """Regression: the Hindi 'October' question once returned the wrong year because the guard only saw Hindi text."""
    p = [dates.Period(type="month", month=10)]
    assert dates.normalize_periods("अक्टूबर में राजस्व", p) == []                      # native text alone: dropped
    out = dates.normalize_periods("Show top 5 products by revenue in October", p)      # the English translation: kept
    assert dates.resolve_all(out, dt.date(2026, 10, 31))[0][0].start == dt.date(2026, 10, 1)
