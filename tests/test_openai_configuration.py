import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pydantic import ValidationError

from config.env_file import update_env
from config.migration import ConfigurationError, migrate_env
from config.settings import Settings, load_settings
from main import run_service
from platform_adapters.windows.composition import build_dependencies
from ui.dashboard import DashboardApp


class OpenAIConfigurationTests(unittest.TestCase):
    def test_v1_migration_preserves_all_values_comments_and_is_idempotent(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {}, clear=True),
        ):
            path = Path(directory) / ".env"
            original = b"# keep\r\nSCHEMA_VERSION=1 # version\r\nGEMINI_API_KEY='old-private-key'\r\nGEMINI_MODEL=custom\r\nACTIVE_PROFILE=example_legend.md\r\nOTHER=keep\r\n"
            path.write_bytes(original)
            settings = load_settings(path)
            self.assertEqual(settings.schema_version, 2)
            self.assertEqual(
                settings.resolve_binding("generation"), ("gemini", "custom")
            )
            self.assertEqual(
                settings.resolve_binding("transcription"), ("gemini", "custom")
            )
            self.assertEqual(
                settings.gemini_api_key.get_secret_value(), "old-private-key"
            )
            migrated = path.read_bytes()
            self.assertEqual(
                migrated, original.replace(b"SCHEMA_VERSION=1", b"SCHEMA_VERSION='2'")
            )
            self.assertFalse(migrate_env(path))
            self.assertEqual(path.read_bytes(), migrated)

    def test_persisted_bindings_resolve_defaults_and_survive_restart(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {}, clear=True),
        ):
            path = Path(directory) / ".env"
            path.write_text(
                "SCHEMA_VERSION=1\nGEMINI_API_KEY=private-gemini\nGENERATION_PROVIDER=openai\nTRANSCRIPTION_PROVIDER=openai\nOPENAI_API_KEY=private-openai\n",
                encoding="utf-8",
            )
            settings = load_settings(path)
            self.assertEqual(
                settings.resolve_binding("generation"), ("openai", "gpt-5.6-terra")
            )
            self.assertEqual(
                settings.resolve_binding("transcription"), ("openai", "gpt-transcribe")
            )
            self.assertTrue(settings.has_api_key)
            first = build_dependencies(settings)
            first.close()
            first.orchestrator.close()
            update_env(
                {
                    "GENERATION_PROVIDER": "gemini",
                    "GENERATION_MODEL": "gemini-3.6-flash",
                },
                path,
            )
            second = build_dependencies(load_settings(path))
            try:
                self.assertEqual(first.provider.binding.provider_id, "openai")
                self.assertEqual(second.provider.binding.provider_id, "gemini")
                self.assertEqual(second.provider.binding.model_id, "gemini-3.6-flash")
                self.assertEqual(second.transcription.binding.provider_id, "openai")
            finally:
                second.close()
                second.orchestrator.close()

    def test_environment_precedence_does_not_overwrite_file_during_migration(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {}, clear=True),
        ):
            path = Path(directory) / ".env"
            path.write_text(
                "SCHEMA_VERSION=1\nGENERATION_PROVIDER=gemini\nGEMINI_API_KEY=file-key\nOPENAI_API_KEY=private-file-key\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"GENERATION_PROVIDER": "openai", "OPENAI_API_KEY": "private-env-key"},
            ):
                settings = load_settings(path)
                self.assertEqual(settings.resolve_binding("generation")[0], "openai")
                self.assertEqual(
                    settings.openai_api_key.get_secret_value(), "private-env-key"
                )
            self.assertIn(
                "OPENAI_API_KEY=private-file-key", path.read_text(encoding="utf-8")
            )
            self.assertIn(
                "GENERATION_PROVIDER=gemini", path.read_text(encoding="utf-8")
            )

    def test_secret_redaction_missing_credentials_and_invalid_configuration(self):
        settings = Settings(
            _env_file=None,
            openai_api_key="private-openai",
            gemini_api_key="private-gemini",
            generation_provider="openai",
            transcription_provider="gemini",
        )
        self.assertNotIn("private", repr(settings) + repr(settings.groups))
        missing = Settings(
            _env_file=None,
            generation_provider="openai",
            transcription_provider="gemini",
        )
        self.assertEqual(
            missing.missing_credentials, ("OPENAI_API_KEY", "GEMINI_API_KEY")
        )
        with self.assertRaises(ValidationError) as error:
            Settings(
                _env_file=None,
                openai_api_key="private-openai",
                generation_provider="claude",
            )
        self.assertNotIn("private-openai", str(error.exception))

    def test_write_failure_and_future_schema_preserve_original(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            original = b"SCHEMA_VERSION=1\nOPENAI_API_KEY=private-test-key\n"
            path.write_bytes(original)
            with patch("config.env_file.replace_file", side_effect=PermissionError()):
                with self.assertRaises(PermissionError):
                    load_settings(path)
            self.assertEqual(path.read_bytes(), original)
            path.write_text("SCHEMA_VERSION=9\n", encoding="utf-8")
            with self.assertRaises(ConfigurationError):
                load_settings(path)

    def test_service_and_dashboard_allow_openai_only_without_gemini_key(self):
        settings = Settings(
            _env_file=None,
            generation_provider="openai",
            transcription_provider="openai",
            openai_api_key="offline-test",
        )
        with (
            patch("main.load_settings", return_value=settings),
            patch("main.validate_hotkeys"),
            patch("main.configure_dpi"),
            patch("main.GhostAssistApp") as app,
        ):
            self.assertEqual(run_service(), 0)
            app.assert_called_once_with(settings)
        dashboard = SimpleNamespace(
            settings=None,
            assistant_process=None,
            session_status=Mock(),
            _show_status=Mock(),
        )
        with (
            patch("ui.dashboard.load_settings", return_value=settings),
            patch("ui.dashboard.validate_hotkeys"),
            patch("ui.dashboard.SingleInstance.exists", return_value=False),
            patch("ui.dashboard.subprocess.Popen") as popen,
            patch("ui.dashboard.Thread"),
        ):
            DashboardApp.toggle_assistant_process(dashboard)
            popen.assert_called_once()

    def test_missing_selected_key_blocks_service_without_printing_values(self):
        settings = Settings(
            _env_file=None,
            generation_provider="openai",
            transcription_provider="openai",
        )
        with (
            patch("main.load_settings", return_value=settings),
            patch("main.validate_hotkeys"),
            patch("main.GhostAssistApp") as app,
            patch("sys.stderr") as stderr,
        ):
            self.assertEqual(run_service(), 2)
            app.assert_not_called()
            self.assertIn(
                "OPENAI_API_KEY", "".join(str(c) for c in stderr.write.call_args_list)
            )
