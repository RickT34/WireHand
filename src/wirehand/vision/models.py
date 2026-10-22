"""Pixel-coordinate observations, independent of capture and input hardware."""
from dataclasses import dataclass
from typing import Protocol

from PIL import Image

Box = tuple[int, int, int, int]  # left, top, right, bottom; exclusive right/bottom


@dataclass(frozen=True)
class Candidate:
    box: Box
    kind: str = 'visual'
    text: str = ''

    @property
    def center(self):
        x1, y1, x2, y2 = self.box
        return ((x1 + x2 - 1) / 2, (y1 + y2 - 1) / 2)


class Detector(Protocol):
    def detect(self, image: Image.Image) -> list[Candidate]: ...


@dataclass(frozen=True)
class Target:
    id: str
    candidate: Candidate


@dataclass
class View:
    id: str
    box: Box
    targets: list[Target]
