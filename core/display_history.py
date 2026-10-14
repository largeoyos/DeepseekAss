"""Account-local, encrypted snapshots of the desktop output transcript."""
import copy
import html as html_module
import re
import uuid
from datetime import datetime

from core.storage import EncryptedStorage


class DisplayHistoryStore:
    def __init__(self, root, crypto=None, enc_key=None):
        self.storage = EncryptedStorage(root, crypto=crypto, enc_key=enc_key)
        self.session_id = uuid.uuid4().hex
        self.created_at = datetime.now().isoformat(timespec="seconds")
        self.entries = []
        self.active_reply = None
        self.dirty = False
        records = self.list_sessions()
        if records:
            self.entries = copy.deepcopy(self.load_session(records[0]["id"]).get("entries", []))

    def list_sessions(self):
        return self.storage.read_json("index.json", default={}).get("sessions", [])

    def load_session(self, session_id):
        # IDs are generated locally, never interpreted as paths supplied by UI text.
        if len(session_id) != 32 or any(c not in "0123456789abcdef" for c in session_id):
            raise ValueError("显示历史 ID 无效")
        return self.storage.read_json(f"sessions/{session_id}.json", default={})

    def begin_reply(self):
        self.active_reply = None

    def append(self, kind, html, source_id=""):
        self.begin_reply()
        if source_id:
            existing = next((entry for entry in self.entries if entry.get("source_id") == source_id), None)
            if existing is not None:
                if existing["html"] != html:
                    existing["html"] = html
                    self.dirty = True
                return existing
            # The plain user bubble may already be visible when a structured
            # conversation assigns its stable message ID at turn completion.
            last = self.entries[-1] if self.entries else None
            def plain(value):
                return "".join(html_module.unescape(re.sub(r"<[^>]*>", "", value)).split())
            if last and not last.get("source_id") and last["kind"] == kind and plain(last["html"]) == plain(html):
                last.update(html=html, source_id=source_id)
                self.dirty = True
                return last
        entry = {"id": uuid.uuid4().hex, "kind": kind, "html": html, "source_id": source_id,
                 "created_at": datetime.now().isoformat(timespec="seconds")}
        self.entries.append(entry)
        self.dirty = True
        return entry

    def update_reply(self, html):
        if self.active_reply is None:
            entry = self.append("assistant", html)
            self.active_reply = entry["id"]
            return entry
        entry = next(item for item in self.entries if item["id"] == self.active_reply)
        if entry["html"] != html:
            entry["html"] = html
            self.dirty = True
        return entry

    def save(self):
        if not self.entries or not self.dirty:
            return
        now = datetime.now().isoformat(timespec="seconds")
        record = {"schema_version": 1, "id": self.session_id, "created_at": self.created_at,
                  "updated_at": now, "entries": self.entries}
        # Commit the snapshot before publishing it in the index. Keep dirty on
        # failure so closing can retry instead of silently discarding the text.
        self.storage.write_json(f"sessions/{self.session_id}.json", record)
        records = [item for item in self.list_sessions() if item["id"] != self.session_id]
        records.insert(0, {key: record[key] for key in ("id", "created_at", "updated_at")}
                       | {"message_count": len(self.entries)})
        self.storage.write_json("index.json", {"schema_version": 1, "sessions": records})
        self.dirty = False
