from pathlib import Path
from typing import Optional

from models.box import Box


class AppState:
    def __init__(self) -> None:
        self.images: dict[Path, Optional[list[Box]]] = {}
        self.current: Optional[Path] = None
