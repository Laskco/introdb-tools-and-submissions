import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
with patch.dict(os.environ, {
    "INTRODB_FFMPEG": sys.executable,
    "INTRODB_MKVEXTRACT": sys.executable,
    "INTRODB_MKVPROPEDIT": sys.executable,
}):
    import detect_op_ed as detector


class ReferenceMatchingTests(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(41)
        self.reference = self.features(200)

    def features(self, frames):
        x = np.exp(self.rng.normal(0, 0.7, (frames, 32)))
        return x / np.linalg.norm(x, axis=1, keepdims=True)

    def test_complete_reference_survives_mix_changes(self):
        target = self.features(600)
        changed = self.reference * np.exp(self.rng.normal(0, 0.06, self.reference.shape))
        changed[40:55] = self.features(15)
        target[137:337] = changed
        result = detector.match_reference_features(target, self.reference)
        self.assertIsNotNone(result)
        self.assertEqual(result["start_frame"], 137)
        self.assertEqual(result["end_frame"], 337)

    def test_unrelated_audio_is_not_a_match(self):
        self.assertIsNone(detector.match_reference_features(self.features(600), self.reference))

    def test_ambiguous_repetitions_are_not_auto_selected(self):
        target = self.features(850)
        target[50:250] = self.reference
        target[550:750] = self.reference
        self.assertIsNone(detector.match_reference_features(target, self.reference))

    def test_reference_longer_than_source_is_rejected(self):
        self.assertIsNone(detector.match_reference_features(self.reference[:90], self.reference))

    def test_partial_tail_does_not_become_a_full_song(self):
        target = self.features(450)
        target[-60:] = self.reference[:60]
        self.assertIsNone(detector.match_reference_features(target, self.reference))

    def test_silence_has_no_match(self):
        self.assertIsNone(detector.match_reference_features(np.zeros((400, 32)), self.reference))
        self.assertIsNone(detector.match_reference_features(self.features(400), np.ones((200, 32))))

    def test_bad_features_fail_clearly(self):
        with self.assertRaises(ValueError):
            detector.match_reference_features(self.features(400), np.ones((200, 31)))
        bad = self.reference.copy()
        bad[0, 0] = np.nan
        with self.assertRaises(ValueError):
            detector.match_reference_features(self.features(400), bad)


if __name__ == "__main__":
    unittest.main()
