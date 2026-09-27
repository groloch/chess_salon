"""Sampling profile for blindfold puzzle difficulty and depth.

A :class:`PuzzleMix` describes the *support* that puzzles are randomly drawn
from: a set of eligible Lichess difficulties and an inclusive depth range. The
trainer keeps sampling randomly; the profile only decides what can be drawn.
"""

from __future__ import annotations

from dataclasses import dataclass
import random


DIFFICULTY_ORDER = ('easiest', 'easier', 'normal', 'harder', 'hardest')
DIFFICULTY_LABELS = {
    'easiest': 'Easiest',
    'easier': 'Easier',
    'normal': 'Normal',
    'harder': 'Harder',
    'hardest': 'Hardest',
}
MIN_DEPTH = 1
MAX_DEPTH = 40


@dataclass(frozen=True)
class PuzzleMix:
    """Eligible difficulties and depth range for random puzzle sampling."""

    difficulties: tuple[str, ...]
    depth_min: int
    depth_max: int

    def __post_init__(self) -> None:
        if isinstance(self.difficulties, str):
            raise ValueError('Choose at least one puzzle difficulty.')
        seen: list[str] = []
        for mode in self.difficulties:
            if mode not in DIFFICULTY_ORDER:
                raise ValueError('Choose a valid puzzle difficulty.')
            if mode not in seen:
                seen.append(mode)
        if not seen:
            raise ValueError('Choose at least one puzzle difficulty.')
        object.__setattr__(self, 'difficulties', tuple(seen))

        for value, label in ((self.depth_min, 'Minimum'), (self.depth_max, 'Maximum')):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f'{label} blindfold depth must be a whole number.')
            if not MIN_DEPTH <= value <= MAX_DEPTH:
                raise ValueError(
                    f'{label} blindfold depth must be between {MIN_DEPTH} and {MAX_DEPTH} plies.'
                )
        if self.depth_min > self.depth_max:
            low, high = self.depth_max, self.depth_min
            object.__setattr__(self, 'depth_min', low)
            object.__setattr__(self, 'depth_max', high)

    @classmethod
    def fixed(cls, mode: str = 'easiest', blindfold_depth: int = 9) -> 'PuzzleMix':
        """Build a one-difficulty, one-depth profile (today's behavior)."""
        return cls((mode,), blindfold_depth, blindfold_depth)

    @classmethod
    def from_config(cls, config) -> 'PuzzleMix':
        """Bridge the legacy ``BlindfoldPuzzleConfig`` object."""
        return cls.fixed(config.mode, config.blindfold_depth)

    @classmethod
    def from_payload(cls, raw: dict) -> 'PuzzleMix':
        """Validate an API payload, accepting the new and legacy shapes."""
        if not isinstance(raw, dict):
            raise ValueError('Invalid puzzle settings.')

        if raw.get('difficulties') is not None:
            difficulties = raw['difficulties']
            if isinstance(difficulties, str) or not isinstance(difficulties, (list, tuple)):
                raise ValueError('Choose at least one puzzle difficulty.')
            depth_min = raw.get('depth_min', raw.get('blindfold_depth', 9))
            depth_max = raw.get('depth_max', depth_min)
        else:
            mode = raw.get('mode', 'easiest')
            blindfold_depth = raw.get('blindfold_depth', 9)
            difficulties = (mode,)
            depth_min = blindfold_depth
            depth_max = blindfold_depth

        return cls(tuple(difficulties), depth_min, depth_max)

    def sample(self, rng: random.Random = random) -> tuple[str, int]:
        """Draw a difficulty and depth from the configured support."""
        mode = rng.choice(self.difficulties)
        depth = rng.randint(self.depth_min, self.depth_max)
        return mode, depth

    def to_payload(self) -> dict:
        return {
            'difficulties': list(self.difficulties),
            'depth_min': self.depth_min,
            'depth_max': self.depth_max,
        }

    def label(self) -> str:
        if len(self.difficulties) == len(DIFFICULTY_ORDER):
            difficulty = 'all difficulties'
        else:
            difficulty = ', '.join(
                DIFFICULTY_LABELS.get(mode, mode) for mode in self.difficulties
            )
        if self.depth_min == self.depth_max:
            depth = f'depth {self.depth_min}'
        else:
            depth = f'depth {self.depth_min}-{self.depth_max}'
        return f'{difficulty} · {depth}'
