"""Bounded, in-memory dialogue sessions."""

from collections import deque
from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True)
class Interaction:
    question: str
    answer: str


class SessionManager:
    def __init__(self, depth=6, max_chars=12000):
        if depth < 1 or max_chars < 1:
            raise ValueError("Session limits must be positive")
        self.depth = depth
        self.max_chars = max_chars
        self._lock = Lock()
        self._generation = 0
        self._histories = {"TEST": deque(), "INTERVIEW": deque()}
        self._closed = False

    def snapshot(self, mode):
        with self._lock:
            return self._generation, tuple(self._histories[mode])

    def add(self, mode, question, answer, generation, cancelled):
        question, answer = question.strip(), answer.strip()
        if not question or not answer:
            return False
        with self._lock:
            if self._closed or cancelled.is_set() or generation != self._generation:
                return False
            if len(question) + len(answer) > self.max_chars:
                return False
            history = self._histories[mode]
            history.append(Interaction(question, answer))
            while (
                len(history) > self.depth
                or sum(len(item.question) + len(item.answer) for item in history)
                > self.max_chars
            ):
                history.popleft()
            return True

    def reset(self):
        with self._lock:
            self._generation += 1
            for history in self._histories.values():
                history.clear()
            self._closed = False

    def close(self):
        with self._lock:
            self._generation += 1
            self._closed = True
            for history in self._histories.values():
                history.clear()
