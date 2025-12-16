from dataclasses import dataclass
from typing import Literal


@dataclass
class Box:
    x: int
    y: int
    w: int
    h: int
    selected: bool = True
    locked: bool = False
    source: Literal["auto", "manual"] = "auto"
