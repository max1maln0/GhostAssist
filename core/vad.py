"""Bounded voice segment detection over 16 kHz mono PCM."""

from collections import deque


class VoiceSegmenter:
    def __init__(
        self,
        detector,
        min_speech_ms=300,
        silence_ms=900,
        max_seconds=30,
        pre_roll_ms=300,
    ):
        self.detector = detector
        self.frame_bytes = 960
        self.min_frames = max(1, (min_speech_ms + 29) // 30)
        self.silence_frames = max(1, (silence_ms + 29) // 30)
        self.max_frames = max(1, int(max_seconds * 1000) // 30)
        self.recent = deque(
            maxlen=min(self.max_frames, max(1, pre_roll_ms // 30) + self.min_frames)
        )
        self.pending = bytearray()
        self.frames = []
        self.voiced = 0
        self.silent = 0
        self.active = False
        self.done = False

    def feed(self, pcm):
        if self.done:
            return
        self.pending.extend(pcm)
        while len(self.pending) >= self.frame_bytes and not self.done:
            frame = bytes(self.pending[: self.frame_bytes])
            del self.pending[: self.frame_bytes]
            voiced = self.detector.is_speech(frame, 16000)
            if not self.active:
                self.recent.append(frame)
                self.voiced = self.voiced + 1 if voiced else 0
                if self.voiced >= self.min_frames:
                    self.active = True
                    self.frames.extend(self.recent)
                    self.recent.clear()
                continue
            self.frames.append(frame)
            self.silent = 0 if voiced else self.silent + 1
            if (
                self.silent >= self.silence_frames
                or len(self.frames) >= self.max_frames
            ):
                self.done = True

    def result(self):
        return b"".join(self.frames) if self.active else b""
