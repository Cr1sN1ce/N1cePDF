"""Per-document collection of edit objects awaiting (or surviving) save. Keyed by stable page keys."""
from __future__ import annotations
from .objects import EditObject, PageKey


class EditLayer:
    def __init__(self) -> None:
        self._objs: dict[str, EditObject] = {}
        self._order: list[str] = []
        self.version = 0  # bumped on every mutation (cache key for previews)

    def __len__(self) -> int:
        return len(self._order)

    def __contains__(self, oid: str) -> bool:
        return oid in self._objs

    def get(self, oid: str) -> EditObject | None:
        return self._objs.get(oid)

    def all(self) -> list[EditObject]:
        return [self._objs[i] for i in self._order]

    def on_page(self, key: PageKey) -> list[EditObject]:
        return [o for o in self.all() if o.page_key == key]

    def add(self, obj: EditObject, index: int | None = None) -> None:
        if obj.id in self._objs:
            raise ValueError("duplicate object id")
        self._objs[obj.id] = obj
        self._order.insert(len(self._order) if index is None else index, obj.id)
        self.version += 1

    def remove(self, oid: str) -> tuple[EditObject, int]:
        obj = self._objs.pop(oid)
        idx = self._order.index(oid)
        del self._order[idx]
        self.version += 1
        return obj, idx

    def replace(self, obj: EditObject) -> EditObject:
        old = self._objs[obj.id]
        self._objs[obj.id] = obj
        self.version += 1
        return old
