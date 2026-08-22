"""Every t() key must exist in BOTH 'en' and 'nl' — prevents production KeyErrors."""
import core
from pathlib import Path


def test_translation_dicts_exist():
    assert isinstance(core._TRANSLATIONS, dict)
    assert "en" in core._TRANSLATIONS and "nl" in core._TRANSLATIONS


def test_en_and_nl_have_identical_key_sets():
    en = set(core._TRANSLATIONS["en"])
    nl = set(core._TRANSLATIONS["nl"])
    missing_nl = sorted(en - nl)
    missing_en = sorted(nl - en)
    assert not missing_nl, f"keys missing in nl: {missing_nl}"
    assert not missing_en, f"keys missing in en: {missing_en}"


def test_all_values_are_nonempty_strings():
    for lang, table in core._TRANSLATIONS.items():
        for k, v in table.items():
            assert isinstance(v, str) and v.strip(), f"{lang}.{k} empty or not a string"


def test_format_placeholders_match_between_langs():
    import re
    ph = re.compile(r"\{(\w+)\}")
    en = core._TRANSLATIONS["en"]
    nl = core._TRANSLATIONS["nl"]
    for k, v in en.items():
        assert set(ph.findall(v)) == set(ph.findall(nl[k])), \
            f"placeholder mismatch for key '{k}'"


def test_t_returns_value_for_every_key():
    core.set_lang("en")
    for k in core._TRANSLATIONS["en"]:
        assert isinstance(core.t(k), str)


def test_t_missing_key_returns_key_itself():
    core.set_lang("en")
    assert core.t("definitely_not_a_key_xyz") == "definitely_not_a_key_xyz"


def test_t_formats_kwargs():
    core.set_lang("en")
    out = core.t("report_status", green="", red="", yellow="", reset="", success=1,
                 failed=2, skipped=3, time=4.5)
    assert "1" in out and "2" in out and "3" in out


def test_set_lang_roundtrip():
    core.set_lang("nl")
    assert core._CURRENT_LANG == "nl"
    core.set_lang("en")
    assert core._CURRENT_LANG == "en"


def test_unknown_lang_is_ignored():
    # set_lang only switches to known languages; unknown keeps current
    core.set_lang("xx")
    assert core._CURRENT_LANG == "en"
    assert core.t("title") == core._TRANSLATIONS["en"]["title"]


def test_every_t_reference_in_source_exists():
    """Regression: keys used via t('key') in shipped code must exist in en dict
    (catches raw-key output like 'act_retry fallback_msg' seen on CNNF)."""
    import re
    repo = Path(__file__).resolve().parent.parent
    used = set()
    for fname in ("engine.py", "justcompiler.py", "docker_manager.py"):
        src = (repo / fname).read_text(encoding="utf-8")
        used |= set(re.findall(r"\bt\(\s*['\"]([\w]+)['\"]", src))
    missing = sorted(k for k in used if k not in core._TRANSLATIONS["en"])
    assert not missing, f"t() keys missing from translations: {missing}"
