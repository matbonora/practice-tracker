"""Settings from environment variables. One place, read once."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo


def _parse_tokens(raw: str) -> dict[str, str]:
    """'web:abc,agent:def' -> {'abc': 'web', 'def': 'agent'} (token -> name)."""
    tokens: dict[str, str] = {}
    for part in filter(None, (p.strip() for p in raw.split(","))):
        name, _, token = part.partition(":")
        if not token:
            raise ValueError(f"PT_TOKENS entry '{part}' must be name:token")
        tokens[token.strip()] = name.strip()
    return tokens


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    presets_dir: Path
    tokens: dict[str, str] = field(repr=False)
    timezone: ZoneInfo
    forgotten_hours: float = 3.0
    min_auto_session_s: int = 120
    auto_sources: tuple[str, ...] = ("midi",)

    db_url_override: str | None = None

    @property
    def db_url(self) -> str:
        return self.db_url_override or f"sqlite:///{self.data_dir / 'app.db'}"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @classmethod
    def from_env(cls) -> "Settings":
        default_presets = Path(__file__).resolve().parents[2] / "presets"
        return cls(
            data_dir=Path(os.environ.get("PT_DATA_DIR", "./data")),
            presets_dir=Path(os.environ.get("PT_PRESETS_DIR", str(default_presets))),
            tokens=_parse_tokens(os.environ.get("PT_TOKENS", "")),
            timezone=ZoneInfo(os.environ.get("PT_TZ", "Europe/Zurich")),
            forgotten_hours=float(os.environ.get("PT_FORGOTTEN_HOURS", "3")),
            min_auto_session_s=int(os.environ.get("PT_MIN_AUTO_SESSION_S", "120")),
            db_url_override=os.environ.get("PT_DB_URL") or None,
        )
