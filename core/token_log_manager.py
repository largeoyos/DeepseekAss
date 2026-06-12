"""
Token usage log persistence.

The manager stores compact request/response previews and real usage values when
the API returns them. Missing usage is recorded explicitly instead of estimated.
"""
import json
import os
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime


@dataclass
class TokenLogEntry:
    id: str
    timestamp: str
    operation: str
    direction: str
    strategy: str
    model: str
    content_preview: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    usage_status: str = "ok"


class TokenLogManager:
    """Stores recent token usage entries for the current user."""

    def __init__(
        self,
        root_dir: str,
        crypto=None,
        enc_key: bytes | None = None,
        max_entries: int = 1000,
    ) -> None:
        self._root_dir = root_dir
        self._crypto = crypto
        self._enc_key = enc_key
        self._max_entries = max_entries
        os.makedirs(self._root_dir, exist_ok=True)

    def _path(self) -> str:
        return os.path.join(self._root_dir, "token_log.json")

    def _actual_path(self) -> str:
        return self._path() + ".enc" if self._enc_key else self._path()

    def _read(self) -> list[dict]:
        path = self._path()
        actual = self._actual_path()
        if not os.path.exists(actual):
            return []
        try:
            if self._enc_key:
                data = self._crypto.decrypt_json(self._enc_key, actual)
            else:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
        except Exception:
            return []
        if isinstance(data, dict):
            return data.get("entries", [])
        if isinstance(data, list):
            return data
        return []

    def _write(self, entries: list[dict]) -> None:
        entries = entries[: self._max_entries]
        data = {"entries": entries}
        if self._enc_key:
            self._crypto.encrypt_json(self._enc_key, self._actual_path(), data)
        else:
            os.makedirs(os.path.dirname(self._path()), exist_ok=True)
            with open(self._path(), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    def list_entries(self) -> list[TokenLogEntry]:
        entries = []
        for raw in self._read():
            try:
                entries.append(TokenLogEntry(**raw))
            except TypeError:
                continue
        return entries

    def add_entry(
        self,
        *,
        operation: str,
        direction: str,
        strategy: str,
        model: str,
        content: str,
        usage: dict | None,
    ) -> TokenLogEntry:
        preview = (content or "").strip().replace("\n", " ")
        if len(preview) > 60:
            preview = preview[:60] + "..."
        status = "ok" if usage else "unavailable"
        entry = TokenLogEntry(
            id=uuid.uuid4().hex,
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            operation=operation,
            direction=direction,
            strategy=strategy,
            model=model,
            content_preview=preview,
            prompt_tokens=usage.get("prompt_tokens") if usage else None,
            completion_tokens=usage.get("completion_tokens") if usage else None,
            total_tokens=usage.get("total_tokens") if usage else None,
            usage_status=status,
        )
        rows = [asdict(entry)] + self._read()
        self._write(rows)
        return entry

    def clear(self) -> None:
        self._write([])

    def totals(self) -> dict[str, int]:
        totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        for entry in self.list_entries():
            if entry.usage_status != "ok":
                continue
            totals["prompt_tokens"] += entry.prompt_tokens or 0
            totals["completion_tokens"] += entry.completion_tokens or 0
            totals["total_tokens"] += entry.total_tokens or 0
        return totals
