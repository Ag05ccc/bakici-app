import contextlib
import io
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent import load_config as load_agent
from registry import load_config as load_server
from tools.make_configs import ROOT, generate_configs


class ConfigurationGeneratorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name) / "private-configs"
        self.template = ROOT / "config" / "server.example.json"

    def generate(self, url="http://192.0.2.10:8001/"):
        return generate_configs(self.template, self.output, url)

    def test_generates_unique_matching_configs_with_private_permissions(self):
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
            files = self.generate()
        self.assertEqual(captured.getvalue(), "")
        self.assertEqual(len(files), 10)
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)
        self.assertTrue(all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in files))
        server = load_server(self.output / "server.local.json")
        keys = [node["api_key"] for node in server["nodes"]]
        self.assertEqual(len(set(keys)), 9)
        self.assertTrue(all(len(key) >= 32 and "example" not in key for key in keys))
        for node in server["nodes"]:
            agent = load_agent(self.output / f"{node['id']}.agent.local.json")
            self.assertEqual(agent.node_id, node["id"])
            self.assertEqual(agent.api_key, node["api_key"])
            self.assertEqual(agent.server_url, "http://192.0.2.10:8001")
            self.assertEqual(agent.enabled_radios, ("bluetooth", "wifi"))
        source = load_server(self.template)
        for original, generated in zip(source["nodes"], server["nodes"]):
            self.assertEqual({k: v for k, v in original.items() if k != "api_key"}, {k: v for k, v in generated.items() if k != "api_key"})

    def test_second_generation_never_overwrites_existing_secrets(self):
        files = self.generate()
        contents = {path.name: path.read_bytes() for path in files}
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.generate()
        self.assertEqual(contents, {path.name: path.read_bytes() for path in files})

    def test_existing_empty_directory_file_and_symlink_are_refused(self):
        self.output.mkdir()
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.generate()
        self.output.rmdir()
        self.output.write_text("existing", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.generate()
        self.assertEqual(self.output.read_text(), "existing")
        self.output.unlink()
        self.output.symlink_to(Path(self.directory.name) / "does-not-exist")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.generate()
        self.assertTrue(self.output.is_symlink())

    def test_invalid_url_or_template_creates_no_output(self):
        for url in (None, "", "file:///tmp/server", "http://user:secret@server", "http://server/path", "http://server:0"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.generate(url)
            self.assertFalse(self.output.exists())
        bad_template = Path(self.directory.name) / "bad.json"
        bad_template.write_text('{"nodes": []}', encoding="utf-8")
        with self.assertRaises(ValueError):
            generate_configs(bad_template, self.output, "http://192.0.2.10:8001")
        self.assertFalse(self.output.exists())

    def test_independent_runs_have_different_keys(self):
        self.generate()
        first = load_server(self.output / "server.local.json")
        self.output = Path(self.directory.name) / "another-private-directory"
        self.generate()
        second = load_server(self.output / "server.local.json")
        self.assertTrue(set(n["api_key"] for n in first["nodes"]).isdisjoint(n["api_key"] for n in second["nodes"]))

    def test_failed_write_removes_partial_set(self):
        with patch("tools.make_configs.json.dump", side_effect=OSError("write failed")):
            with self.assertRaises(OSError):
                self.generate()
        self.assertFalse(self.output.exists())

    def test_cli_runs_without_site_packages_and_never_prints_keys(self):
        result = subprocess.run([sys.executable, "-S", str(ROOT / "tools" / "make_configs.py"), "--server-url", "http://192.0.2.10:8001", "--output", str(self.output)], capture_output=True, text=True, timeout=5, cwd=self.directory.name)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Created 10 private configuration files", result.stdout)
        self.assertEqual(result.stderr, "")
        for node in load_server(self.output / "server.local.json")["nodes"]:
            self.assertNotIn(node["api_key"], result.stdout + result.stderr)
        repeated = subprocess.run([sys.executable, "-S", str(ROOT / "tools" / "make_configs.py"), "--server-url", "http://192.0.2.10:8001", "--output", str(self.output)], capture_output=True, text=True, timeout=5)
        self.assertEqual(repeated.returncode, 2)
        self.assertIn("already exists", repeated.stderr)
        for node in load_server(self.output / "server.local.json")["nodes"]:
            self.assertNotIn(node["api_key"], repeated.stdout + repeated.stderr)


if __name__ == "__main__":
    unittest.main()
