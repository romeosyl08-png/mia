import tempfile
import unittest
import io
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from barrassons.cli import DEFAULT_API_BASE, _resolve, _select_model, api_request, connect, execute_tool, main, normalize_api_base, resolve_api_base


class ConnectionTests(unittest.TestCase):
    def test_site_url_gets_the_api_path_and_local_urls_are_allowed(self):
        self.assertEqual(normalize_api_base('https://example.test/'), 'https://example.test/v1')
        self.assertEqual(normalize_api_base('http://127.0.0.1:8000/v1/'), 'http://127.0.0.1:8000/v1')
        self.assertEqual(normalize_api_base('https://example.test/api/v1/'), 'https://example.test/api/v1')

    def test_invalid_address_never_exposes_its_value_or_contacts_the_server(self):
        for value in ['sk-ai2ai-synthetic-secret', 'ftp://example.test', 'https://user:secret@example.test/v1',
                      'https://example.test/v1?key=secret', 'https://example.test:bad/v1', 'https://[invalid', None]:
            with self.subTest(value=value), patch('barrassons.cli.urllib.request.urlopen') as request:
                with self.assertRaises(ValueError) as error:
                    api_request({'api_base': value, 'api_key': 'synthetic-key'}, '/models')
                self.assertNotIn('synthetic-secret', str(error.exception))
                self.assertNotIn('key=secret', str(error.exception))
                self.assertIn('barrassons login', str(error.exception))
                request.assert_not_called()

    def test_invalid_saved_address_uses_render_without_a_prompt(self):
        with patch.dict('os.environ', {}, clear=True), patch('builtins.input') as ask:
            self.assertEqual(resolve_api_base({'api_base': 'sk-ai2ai-saved-secret'}), DEFAULT_API_BASE)
        ask.assert_not_called()

    def test_explicit_server_and_environment_take_priority(self):
        with patch.dict('os.environ', {'BARRASSONS_API_BASE': 'https://environment.test/v1'}):
            self.assertEqual(resolve_api_base({}), 'https://environment.test/v1')
            self.assertEqual(resolve_api_base({}, 'http://127.0.0.1:8000'), 'http://127.0.0.1:8000/v1')
            with self.assertRaises(ValueError):
                resolve_api_base({}, 'sk-ai2ai-invalid')

    def test_login_reads_the_key_without_echo_and_saves_the_correct_fields(self):
        output = io.StringIO()
        with patch('builtins.input', side_effect=['2']) as ask, \
                patch('barrassons.cli.getpass.getpass', return_value='synthetic-private-key') as read_key, \
                patch('barrassons.cli.api_request', return_value={'data': []}) as request, \
                patch('barrassons.cli.save_config') as save, redirect_stdout(output), \
                patch.dict('os.environ', {}, clear=True):
            value = connect({})
        self.assertEqual(value['api_base'], DEFAULT_API_BASE)
        self.assertEqual(value['api_key'], 'synthetic-private-key')
        read_key.assert_called_once()
        save.assert_called_once_with(value)
        ask.assert_called_once()
        request.assert_called_once_with(value, '/models')
        self.assertNotIn('synthetic-private-key', output.getvalue())

    def test_failed_login_preserves_the_existing_connection(self):
        config = {'api_base': DEFAULT_API_BASE, 'api_key': 'previous-key'}
        with patch('builtins.input', return_value='2'), \
                patch('barrassons.cli.getpass.getpass', return_value='invalid-new-key'), \
                patch('barrassons.cli.api_request', side_effect=RuntimeError('Clé refusée')), \
                patch('barrassons.cli.save_config') as save, redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):
                connect(config)
        save.assert_not_called()
        self.assertEqual(config['api_key'], 'previous-key')

    def test_login_command_finishes_without_starting_a_session(self):
        config = {'api_base': DEFAULT_API_BASE, 'api_key': 'fixture'}
        with patch('sys.argv', ['barrassons', 'login']), patch('barrassons.cli.load_config', return_value=config), \
                patch('barrassons.cli.connect', return_value=config), patch('barrassons.cli._select_model') as select, \
                patch('barrassons.cli.run_session') as session:
            self.assertEqual(main(), 0)
        select.assert_not_called(); session.assert_not_called()


class ModelPickerTests(unittest.TestCase):
    def setUp(self):
        self.models = [{'id': f'model-{index:02}', 'owned_by': 'openrouter'} for index in range(30)]

    def test_saved_model_starts_without_another_selection(self):
        with patch('barrassons.cli.api_request', return_value={'data': self.models}), patch('builtins.input') as ask, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(_select_model({'model': 'model-12'}), 'model-12')
        ask.assert_not_called()

    def test_pagination_and_search_keep_catalog_numbers(self):
        output = io.StringIO()
        with patch('barrassons.cli.api_request', return_value={'data': self.models}), \
                patch('builtins.input', side_effect=['/suivant', 'model-2', '28']), redirect_stdout(output):
            self.assertEqual(_select_model({}), 'model-27')
        self.assertIn('Page 1/3', output.getvalue())
        self.assertIn('Page 2/3', output.getvalue())
        self.assertIn('Page 1/1', output.getvalue())

    def test_explicit_model_is_validated_without_input(self):
        with patch('barrassons.cli.api_request', return_value={'data': self.models}), patch('builtins.input') as ask, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(_select_model({}, requested='model-22'), 'model-22')
            with self.assertRaises(ValueError):
                _select_model({}, requested='missing')
        ask.assert_not_called()


class LocalToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.config_env = patch.dict('os.environ', {'XDG_CONFIG_HOME': str(self.root / 'config')})
        self.config_env.start()

    def tearDown(self):
        self.config_env.stop()
        self.temp_dir.cleanup()

    def test_paths_cannot_escape_project(self):
        with self.assertRaises(ValueError):
            _resolve(self.root, "../outside.txt")

    def test_write_requires_confirmation(self):
        with patch("builtins.input", return_value="n"):
            result = execute_tool("write_file", {"path": "new.txt", "content": "hello"}, self.root)
        self.assertEqual(result["status"], "refused")
        self.assertFalse((self.root / "new.txt").exists())

    def test_search_finds_root_files_and_skips_virtualenv(self):
        (self.root / "sample.py").write_text("needle\n", encoding="utf-8")
        hidden = self.root / "venv" / "lib.py"
        hidden.parent.mkdir()
        hidden.write_text("needle\n", encoding="utf-8")
        result = execute_tool("search_code", {"query": "needle", "glob": "**/*.py"}, self.root)
        self.assertEqual([match["path"] for match in result["matches"]], ["sample.py"])

    def test_edit_requires_a_unique_match_and_confirmation(self):
        target = self.root / "sample.py"
        target.write_text("value = 1\n", encoding="utf-8")
        with patch("builtins.input", return_value="y"):
            result = execute_tool("edit_file", {
                "path": "sample.py", "old_text": "value = 1", "new_text": "value = 2",
            }, self.root)
        self.assertEqual(result["status"], "updated")
        self.assertEqual(target.read_text(encoding="utf-8"), "value = 2\n")


if __name__ == "__main__":
    unittest.main()
