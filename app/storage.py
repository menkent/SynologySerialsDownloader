import asyncio
import os
from pathlib import Path

from .models import State


class Store:
    """Единый state.json (см. ADR-0001): атомарная запись, один процесс-писатель.

    Любая мутация state и последующий save() выполняются под self.lock.
    """

    def __init__(self, path: Path):
        self.path = path
        self.lock = asyncio.Lock()
        if path.exists():
            self.state = State.model_validate_json(path.read_text(encoding="utf-8"))
        else:
            self.state = State()

    async def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(self.state.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
