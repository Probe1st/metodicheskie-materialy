from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True, slots=True)
class Catalog:
    groups: tuple[str, ...]
    disciplines: Mapping[str, tuple[str, ...]]

    def contains(self, group: str, discipline: str, work: str) -> bool:
        return group in self.groups and work in self.disciplines.get(discipline, ())


def load_catalog(path: Path) -> Catalog:
    data = json.loads(path.read_text(encoding="utf-8"))
    groups = data.get("groups")
    disciplines = data.get("disciplines")
    if not isinstance(groups, list) or not groups or not all(isinstance(item, str) and item for item in groups):
        raise ValueError("catalog groups must be a non-empty string list")
    if not isinstance(disciplines, dict) or not all(
        isinstance(name, str) and name and isinstance(works, list) and all(isinstance(work, str) and work for work in works)
        for name, works in disciplines.items()
    ):
        raise ValueError("catalog disciplines must map names to string lists")
    return Catalog(tuple(groups), {name: tuple(works) for name, works in disciplines.items()})
