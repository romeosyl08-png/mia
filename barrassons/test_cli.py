import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from barrassons.cli import _resolve, execute_tool


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
