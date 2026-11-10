import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
with patch.dict(os.environ, {
    "INTRODB_FFMPEG": sys.executable,
    "INTRODB_MKVEXTRACT": sys.executable,
    "INTRODB_MKVPROPEDIT": sys.executable,
}):
    import detect_op_ed as detector


class ChapterWritingTests(unittest.TestCase):
    def test_titles_round_trip_as_xml_text(self):
        labels = ['Crisis & Conspiracy', '<Ending> "Part 1"', 'Title \u2013 Credits']
        chapters = [(i * 90.0, label) for i, label in enumerate(labels)]
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "episode.mkv"
            source.write_bytes(b"unchanged media")

            def run(command, **kwargs):
                self.assertEqual(command[1:3], [str(source), "--chapters"])
                root = ET.parse(command[3]).getroot()
                self.assertEqual([
                    node.text for node in root.findall(".//ChapterString")
                ], labels)
                self.assertEqual(root.findtext(".//ChapterTimeStart"), "00:00:00.000")
                return SimpleNamespace(returncode=0)

            with patch.object(detector.subprocess, "run", side_effect=run):
                self.assertTrue(detector.write_existing_chapters(str(source), chapters))
            self.assertEqual(source.read_bytes(), b"unchanged media")
            self.assertFalse(Path(str(source) + ".chapters.xml").exists())

    def test_failed_edit_removes_temporary_xml(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "episode.mkv"
            with patch.object(detector.subprocess, "run", return_value=SimpleNamespace(returncode=1)):
                self.assertFalse(detector.write_existing_chapters(str(source), [(0, "OP & ED")]))
            self.assertFalse(Path(str(source) + ".chapters.xml").exists())


if __name__ == "__main__":
    unittest.main()
