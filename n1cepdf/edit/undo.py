"""Command-based undo/redo (Qt-free). Commands change the document model structurally; no raster
snapshots are ever used."""
from __future__ import annotations
from typing import Callable


class Command:
    text = "Edit"
    structural = False          # True if the PDF page tree / content changed (viewer must reload)

    def do(self) -> None:
        raise NotImplementedError

    def undo(self) -> None:
        raise NotImplementedError

    def merge_key(self) -> object | None:
        """Consecutive commands with the same non-None key may merge (typing, dragging)."""
        return None

    def merge_from(self, newer: "Command") -> None:
        raise NotImplementedError


class MacroCommand(Command):
    def __init__(self, text: str, commands: list[Command]):
        self.text = text
        self.commands = commands
        self.structural = any(c.structural for c in commands)

    def do(self) -> None:
        done: list[Command] = []
        try:
            for c in self.commands:
                c.do()
                done.append(c)
        except Exception:
            for c in reversed(done):  # roll back a half-applied macro
                c.undo()
            raise

    def undo(self) -> None:
        for c in reversed(self.commands):
            c.undo()


class UndoStack:
    def __init__(self, on_change: Callable[[Command, str], None] | None = None, limit: int = 200):
        self._cmds: list[Command] = []
        self._index = 0            # number of applied commands
        self._clean = 0
        self._clean_lost = False   # clean state was discarded by truncating redo history
        self._limit = limit
        self._on_change = on_change
        self._last_merge_ok = False  # merging allowed only right after a push

    # ---- state ---------------------------------------------------------
    @property
    def index(self) -> int:
        return self._index

    @property
    def can_undo(self) -> bool:
        return self._index > 0

    @property
    def can_redo(self) -> bool:
        return self._index < len(self._cmds)

    @property
    def undo_text(self) -> str:
        return self._cmds[self._index - 1].text if self.can_undo else ""

    @property
    def redo_text(self) -> str:
        return self._cmds[self._index].text if self.can_redo else ""

    def is_clean(self) -> bool:
        return not self._clean_lost and self._index == self._clean

    def set_clean(self) -> None:
        self._clean, self._clean_lost = self._index, False
        self._last_merge_ok = False

    def break_merge(self) -> None:
        self._last_merge_ok = False

    # ---- operations ----------------------------------------------------
    def push(self, cmd: Command) -> None:
        """Execute cmd and record it. If do() raises, nothing is recorded."""
        key = cmd.merge_key()
        if (key is not None and self._last_merge_ok and self._index == len(self._cmds)
                and self._index > 0 and self._cmds[-1].merge_key() == key
                and (self._clean != self._index or self._clean_lost)):
            cmd.do()
            self._cmds[-1].merge_from(cmd)
            self._notify(cmd, "do")
            return
        cmd.do()
        if self._index < len(self._cmds):
            if self._clean > self._index:
                self._clean_lost = True   # the clean state lived in the discarded redo tail
            del self._cmds[self._index:]
        self._cmds.append(cmd)
        self._index += 1
        if len(self._cmds) > self._limit:
            drop = len(self._cmds) - self._limit
            del self._cmds[:drop]
            self._index -= drop
            if self._clean >= drop:
                self._clean -= drop
            else:
                self._clean_lost = True
        self._last_merge_ok = key is not None
        self._notify(cmd, "do")

    def undo(self) -> bool:
        if not self.can_undo:
            return False
        cmd = self._cmds[self._index - 1]
        cmd.undo()
        self._index -= 1
        self._last_merge_ok = False
        self._notify(cmd, "undo")
        return True

    def redo(self) -> bool:
        if not self.can_redo:
            return False
        cmd = self._cmds[self._index]
        cmd.do()
        self._index += 1
        self._last_merge_ok = False
        self._notify(cmd, "redo")
        return True

    def _notify(self, cmd: Command, how: str) -> None:
        if self._on_change is not None:
            self._on_change(cmd, how)
