import unittest

from core.vad import VoiceSegmenter


class Detector:
    def is_speech(self, frame, rate):
        return frame[0] == 1


class VoiceTests(unittest.TestCase):
    def test_noise_silence_and_end(self):
        vad = VoiceSegmenter(Detector(), min_speech_ms=60, silence_ms=60)
        silence = bytes(960)
        speech = bytes([1]) * 960
        vad.feed(speech + silence * 20)
        self.assertFalse(vad.active)
        vad.feed(speech * 2 + silence * 2)
        self.assertTrue(vad.done)
        self.assertTrue(vad.result())

    def test_max_length_and_repeated_input(self):
        vad = VoiceSegmenter(Detector(), min_speech_ms=30, max_seconds=0.12)
        vad.feed(bytes([1]) * 960 * 100)
        self.assertTrue(vad.done)
        before = vad.result()
        vad.feed(bytes([1]) * 960)
        self.assertEqual(vad.result(), before)
        self.assertLessEqual(len(before), 960 * 5)


if __name__ == "__main__":
    unittest.main()
