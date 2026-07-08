import unittest
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import UI, t, set_lang, _TRANSLATIONS


class TestTranslations(unittest.TestCase):
    def test_english_default(self):
        set_lang("en")
        self.assertEqual(t("title"), "JustCompiler Engine Dashboard")

    def test_dutch_translation(self):
        set_lang("nl")
        self.assertEqual(t("title"), "JustCompiler Engine Dashboard")
        self.assertEqual(t("menu_1"), "1. Lokale workspace compileren")

    def test_fallback_key(self):
        set_lang("en")
        self.assertEqual(t("nonexistent_key"), "nonexistent_key")

    def test_format_args(self):
        set_lang("en")
        result = t("settings_lang", lang="English")
        self.assertIn("English", result)

    def test_unknown_lang_falls_back(self):
        set_lang("en")
        set_lang("fr")
        self.assertEqual(t("menu_1"), "1. Compile local workspace")


class TestUI(unittest.TestCase):
    def test_log_methods_exist(self):
        self.assertTrue(hasattr(UI, "info"))
        self.assertTrue(hasattr(UI, "success"))
        self.assertTrue(hasattr(UI, "warn"))
        self.assertTrue(hasattr(UI, "error"))
        self.assertTrue(hasattr(UI, "clear"))
        self.assertTrue(hasattr(UI, "draw_panel"))

    def test_color_constants(self):
        self.assertIsNotNone(UI.CYAN)
        self.assertIsNotNone(UI.RESET)
        self.assertIsNotNone(UI.GREEN)
        self.assertIsNotNone(UI.RED)
        self.assertIsNotNone(UI.YELLOW)
        self.assertIsNotNone(UI.BOLD)
        self.assertIsNotNone(UI.DIM)

    def test_spinner_context(self):
        spinner = UI.spinner("test")
        self.assertIsNotNone(spinner)
        with spinner:
            pass
        self.assertTrue(spinner.success)


class TestSetLang(unittest.TestCase):
    def test_set_lang_valid(self):
        set_lang("en")
        self.assertEqual(t("title"), "JustCompiler Engine Dashboard")

    def test_set_lang_invalid(self):
        set_lang("en")
        set_lang("de")
        self.assertEqual(t("title"), "JustCompiler Engine Dashboard")


class TestTranslationsDict(unittest.TestCase):
    def test_translations_have_same_keys(self):
        en_keys = set(_TRANSLATIONS["en"].keys())
        nl_keys = set(_TRANSLATIONS["nl"].keys())
        missing_in_nl = en_keys - nl_keys
        missing_in_en = nl_keys - en_keys
        self.assertEqual(missing_in_nl, set(),
                         f"Keys in EN but missing in NL: {missing_in_nl}")
        self.assertEqual(missing_in_en, set(),
                         f"Keys in NL but missing in EN: {missing_in_en}")


if __name__ == "__main__":
    unittest.main()
