import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from config.env_file import update_env
from config.migration import ConfigurationError, migrate_env
from config.settings import Settings, load_settings


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / ".env"
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_legacy_migration_preserves_values_comments_and_profiles(self):
        original = "# comment\nGEMINI_API_KEY='private-test-key'\nGEMINI_MODEL=custom\nAUDIO_DEVICE='Headset'\nHOTKEY_SCREEN='<alt>+z'\nACTIVE_PROFILE=my.md\nUNKNOWN=keep\n"
        self.path.write_text(original, encoding="utf-8")
        original_bytes = self.path.read_bytes()
        profile = self.path.parent / "my.md"
        profile.write_text("private profile", encoding="utf-8")
        settings = load_settings(self.path)
        self.assertEqual(settings.gemini_api_key.get_secret_value(), "private-test-key")
        self.assertEqual(settings.groups.provider.model, "custom")
        self.assertEqual(settings.hotkey_screen, "<alt>+z")
        self.assertEqual(settings.groups.audio.device, "Headset")
        migrated = self.path.read_bytes()
        self.assertIn(original_bytes, migrated)
        self.assertIn(b"SCHEMA_VERSION='1'", migrated)
        self.assertFalse(migrate_env(self.path))
        self.assertEqual(self.path.read_bytes(), migrated)
        self.assertEqual(profile.read_text(encoding="utf-8"), "private profile")
        self.assertNotIn("private-test-key", repr(settings) + repr(settings.groups))

    def test_missing_file_and_defaults(self):
        settings = load_settings(self.path)
        self.assertEqual(settings.schema_version, 1)
        self.assertFalse(self.path.exists())
        self.assertEqual(settings.groups.memory.depth, 6)

    def test_future_duplicate_and_invalid_versions_never_written(self):
        for content in (
            "SCHEMA_VERSION=9\n",
            "SCHEMA_VERSION=1\nSCHEMA_VERSION=1\n",
            "SCHEMA_VERSION=oops\n",
        ):
            with self.subTest(content=content):
                self.path.write_text(content, encoding="utf-8")
                with self.assertRaises(ConfigurationError):
                    load_settings(self.path)
                with self.assertRaises(ConfigurationError):
                    update_env({"GEMINI_MODEL": "new"}, self.path)
                self.assertEqual(self.path.read_text(encoding="utf-8"), content)

    def test_bad_encoding_and_syntax(self):
        for data in (b"GEMINI_API_KEY='unterminated\n", b"\xff\xfeinvalid"):
            self.path.write_bytes(data)
            with self.assertRaises(ConfigurationError):
                load_settings(self.path)
            self.assertEqual(self.path.read_bytes(), data)

    def test_invalid_values_block_migration_even_with_environment_override(self):
        self.path.write_text("AUDIO_MAX_SECONDS=500\n", encoding="utf-8")
        with patch.dict(os.environ, {"AUDIO_MAX_SECONDS": "10"}):
            with self.assertRaises(ValidationError):
                load_settings(self.path)
        self.assertNotIn("SCHEMA_VERSION", self.path.read_text(encoding="utf-8"))

    def test_failed_write_preserves_original_and_cleans_temporary_file(self):
        original = b"GEMINI_API_KEY='private-test-key'\n"
        self.path.write_bytes(original)
        with patch("config.env_file.os.replace", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                load_settings(self.path)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_environment_precedence_and_raw_file_version(self):
        self.path.write_text("GEMINI_MODEL=file-model\n", encoding="utf-8")
        with patch.dict(os.environ, {"GEMINI_MODEL": "environment-model"}):
            self.assertEqual(load_settings(self.path).gemini_model, "environment-model")
        self.assertIn("GEMINI_MODEL=file-model", self.path.read_text(encoding="utf-8"))
        self.path.write_text("SCHEMA_VERSION=9\n", encoding="utf-8")
        with patch.dict(os.environ, {"SCHEMA_VERSION": "1"}):
            with self.assertRaises(ConfigurationError):
                load_settings(self.path)

    def test_secret_validation_error_does_not_include_key(self):
        with self.assertRaises(ValidationError) as error:
            Settings(
                _env_file=None, gemini_api_key="private-test-key", schema_version=9
            )
        self.assertNotIn("private-test-key", str(error.exception))

    def test_dotenv_interpolation_and_unknown_environment_version(self):
        self.path.write_text("BASE=20\nTASK_TIMEOUT=${BASE}\n", encoding="utf-8")
        self.assertEqual(load_settings(self.path).task_timeout, 20)
        self.assertIn("TASK_TIMEOUT=${BASE}", self.path.read_text(encoding="utf-8"))
        before = self.path.read_bytes()
        with patch.dict(os.environ, {"SCHEMA_VERSION": "2"}):
            with self.assertRaises(ValidationError):
                load_settings(self.path)
        self.assertEqual(self.path.read_bytes(), before)
