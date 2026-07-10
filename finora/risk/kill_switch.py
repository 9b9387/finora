"""File-based kill switch.

The switch is a marker file (state_dir/KILL) so a human — or any process —
can halt trading with `touch` even when Python is wedged. Presence of the
file is the single source of truth; its JSON body is informational.
"""
from __future__ import annotations

import json
from pathlib import Path

from finora.core.log import get_logger
from finora.core.models import utc_now

logger = get_logger(__name__)

_KILL_FILENAME = "KILL"


class KillSwitch:
    def __init__(self, state_dir: Path) -> None:
        self._path = Path(state_dir) / _KILL_FILENAME

    @property
    def path(self) -> Path:
        return self._path

    def engaged(self) -> bool:
        return self._path.exists()

    def engage(self, reason: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps({"reason": reason, "engaged_at": utc_now().isoformat()})
        )
        logger.warning("kill switch engaged", reason=reason, path=str(self._path))

    def release(self) -> None:
        if self._path.exists():
            self._path.unlink()
            logger.warning("kill switch released", path=str(self._path))

    def status(self) -> dict | None:
        """The engage payload, or None when not engaged. A hand-touched or
        corrupt KILL file still reports as engaged, with its raw content."""
        if not self._path.exists():
            return None
        raw = self._path.read_text()
        try:
            doc = json.loads(raw)
            if isinstance(doc, dict):
                return doc
        except json.JSONDecodeError:
            pass
        return {"reason": raw.strip() or "unknown (empty KILL file)", "engaged_at": None}
