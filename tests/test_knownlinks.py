import tempfile
import unittest
from pathlib import Path

from app.knownlinks import KnownlinksError, parse_knownlinks
from app.config import Settings
from unittest.mock import patch


class KnownlinksTests(unittest.TestCase):
    def parse(self, text):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory) / "knownlinks"
            path.write_text(text, encoding="utf-8")
            return parse_knownlinks(path)

    def test_rename_preserves_identity(self):
        before = self.parse('192.0.2.1 1 stable "Old name" aabbcc 100\n')[0]
        after = self.parse('192.0.2.2 2 stable "New name" aabbcc ignored\n')[0]
        self.assertEqual(before.link_id, after.link_id)
        self.assertEqual(after.name, "New name")

    def test_invalid_rows(self):
        for row in ('broken', '192.0.2.1 0 a Name aabbcc 100',
                    'invalid 1 a Name aabbcc 100', '192.0.2.1 1 a Name nope 100',
                    '192.0.2.1 1 a "unclosed aabbcc 100'):
            with self.subTest(row=row), self.assertRaisesRegex(KnownlinksError, "line 1"):
                self.parse(row)

    def test_duplicate_id_matching_metadata(self):
        links = self.parse('192.0.2.1 1 a Name aabbcc 100\n192.0.2.1 2 a Name aabbcc unused\n')
        self.assertEqual(len(links), 1)

    def test_duplicate_id_conflicts(self):
        for metadata in ('Other aabbcc', 'Name 112233'):
            with self.subTest(metadata=metadata), self.assertRaisesRegex(KnownlinksError, "line 2: conflicting.*line 1"):
                self.parse(f'192.0.2.1 1 a Name aabbcc 100\n192.0.2.1 2 a {metadata} 100\n')

    def test_duplicate_endpoint(self):
        with self.assertRaisesRegex(KnownlinksError, "duplicate endpoint"):
            self.parse('192.0.2.1 1 a Name aabbcc 100\n' * 2)

    def test_comments_and_links_without_traffic(self):
        links = self.parse('# comment\n\n192.0.2.1 1 idle "No traffic" AABBCC unused # end\n')
        self.assertEqual(links[0].link_id, "idle")
        self.assertEqual(links[0].color, "#aabbcc")

    def test_line_limit(self):
        with self.assertRaisesRegex(KnownlinksError, "4096"):
            self.parse('#' * 4097)

    def test_vm_configuration(self):
        with patch.dict('os.environ', {'ASSTAT_VM_URL': 'http://127.0.0.1:8428'}):
            self.assertEqual(Settings.from_env().victoriametrics_url, 'http://127.0.0.1:8428')
        with patch.dict('os.environ', {'ASSTAT_VM_URL': 'http://192.0.2.1:8428'}):
            with self.assertRaises(ValueError):
                Settings.from_env()
