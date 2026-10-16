from __future__ import annotations

from bot.i18n import (
    _STRINGS,
    DE,
    EN,
    FA,
    LANGUAGE_NAMES,
    SUPPORTED_LANGUAGES,
    get_text,
    language_code_to_code,
)


def test_all_languages_define_same_keys():
    base = set(_STRINGS[EN])
    for lang in SUPPORTED_LANGUAGES:
        assert set(_STRINGS[lang]) == base, f"{lang} key set differs"


def test_supported_languages_are_complete():
    assert SUPPORTED_LANGUAGES == ("en", "de", "fa")
    assert set(LANGUAGE_NAMES) == set(SUPPORTED_LANGUAGES)


def test_get_text_formats_kwargs():
    out = get_text(EN, "language_set", lang="Deutsch")
    assert "Deutsch" in out


def test_get_text_falls_back_to_default_then_english():
    from bot.i18n import get_text as gt

    assert gt("xx", "language_set", DE, lang="Deutsch") == _STRINGS[DE]["language_set"].format(lang="Deutsch")
    assert gt("xx", "language_set", None, lang="Deutsch").startswith("Language set")


def test_get_text_unknown_key_returns_key():
    assert get_text(EN, "definitely_missing_key") == "definitely_missing_key"


def test_podcast_too_large_link_present_and_formats():
    # The publisher link fallback must exist in every language and interpolate.
    for lang in SUPPORTED_LANGUAGES:
        out = get_text(lang, "podcast_too_large_link", max=500, url="https://cdn.example.com/a.mp3")
        assert "500" in out
        assert "https://cdn.example.com/a.mp3" in out


def test_get_text_never_crashes_on_bad_kwargs():
    text = get_text(EN, "language_set")
    assert "{" in text  # placeholder left untouched, not an exception


def test_language_code_to_code():
    assert language_code_to_code("fa", "en") == FA
    assert language_code_to_code("fa-IR", "en") == FA
    assert language_code_to_code("de", "en") == DE
    assert language_code_to_code("en-GB", "de") == EN
    assert language_code_to_code(None, "en") == "en"
    assert language_code_to_code("zz", "de") == DE
    assert language_code_to_code("", "en") == "en"
