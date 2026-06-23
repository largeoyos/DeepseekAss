from __future__ import annotations

import uuid
from dataclasses import asdict

from core.agent.types import AgentRun, AgentSession, ChangeOperation, ChangeSet, now_iso


class AgentRepository:
    """Encrypted persistence scoped to one book workspace."""

    def __init__(self, workspace) -> None:
        self.workspace = workspace
        self.storage = workspace.storage
        self.root = workspace.agent_root

    def create_session(self, book_id: str, book_title: str, agent_kind: str, title: str) -> AgentSession:
        session = AgentSession(f"session_{uuid.uuid4().hex}", book_id, book_title, agent_kind, title.strip() or "新 Agent 会话")
        self.save_session(session)
        return session

    def save_session(self, session: AgentSession) -> None:
        session.updated_at = now_iso()
        self.storage.write_json(self._session_path(session.session_id), asdict(session))

    def load_session(self, session_id: str) -> AgentSession | None:
        data = self.storage.read_json(self._session_path(session_id))
        return AgentSession(**data) if isinstance(data, dict) else None

    def list_sessions(self) -> list[AgentSession]:
        result = []
        for path in self.storage.list_files(f"{self.root}/sessions"):
            data = self.storage.read_json(path)
            if isinstance(data, dict):
                try:
                    result.append(AgentSession(**data))
                except TypeError:
                    pass
        return sorted(result, key=lambda item: item.updated_at, reverse=True)

    def save_run(self, run: AgentRun) -> None:
        run.updated_at = now_iso()
        self.storage.write_json(self._run_path(run.run_id), asdict(run))

    def load_run(self, run_id: str) -> AgentRun | None:
        data = self.storage.read_json(self._run_path(run_id))
        return AgentRun(**data) if isinstance(data, dict) else None

    def save_checkpoint(self, run: AgentRun, reason: str) -> str:
        checkpoint_id = f"cp_{run.iteration:03d}_{uuid.uuid4().hex[:8]}"
        self.storage.write_json(f"{self.root}/checkpoints/{run.run_id}/{checkpoint_id}.json", {"checkpoint_id": checkpoint_id, "reason": reason, "saved_at": now_iso(), "run": asdict(run)})
        self.storage.write_json(f"{self.root}/checkpoints/{run.run_id}/latest.json", {"checkpoint_id": checkpoint_id})
        return checkpoint_id

    def load_latest_checkpoint(self, run_id: str) -> AgentRun | None:
        latest = self.storage.read_json(f"{self.root}/checkpoints/{run_id}/latest.json")
        if not isinstance(latest, dict):
            return None
        data = self.storage.read_json(f"{self.root}/checkpoints/{run_id}/{latest.get('checkpoint_id')}.json")
        return AgentRun(**data["run"]) if isinstance(data, dict) and isinstance(data.get("run"), dict) else None

    def append_event(self, run_id: str, event: dict) -> None:
        path = f"{self.root}/ledger/{run_id}.json"
        ledger = self.storage.read_json(path, default={"schema_version": 1, "events": []})
        if not isinstance(ledger, dict):
            ledger = {"schema_version": 1, "events": []}
        ledger.setdefault("events", []).append(event)
        self.storage.write_json(path, ledger)

    def save_artifact(self, run_id: str, kind: str, content: str, metadata: dict | None = None) -> str:
        artifact_id = f"artifact_{uuid.uuid4().hex}"
        self.storage.write_json(f"{self.root}/artifacts/{artifact_id}.json", {"schema_version": 1, "artifact_id": artifact_id, "run_id": run_id, "kind": kind, "content": content, "metadata": metadata or {}, "created_at": now_iso()})
        return artifact_id

    def load_artifact(self, artifact_id: str) -> dict | None:
        data = self.storage.read_json(f"{self.root}/artifacts/{artifact_id}.json")
        return data if isinstance(data, dict) else None

    def list_artifacts(self, kind: str | None = None) -> list[dict]:
        result = []
        for path in self.storage.list_files(f"{self.root}/artifacts"):
            if not path.endswith(".json"):
                continue
            data = self.storage.read_json(path)
            if isinstance(data, dict) and (kind is None or data.get("kind") == kind):
                result.append(data)
        return sorted(result, key=lambda item: item.get("created_at", ""), reverse=True)

    def save_draft(self, run_id: str, name: str, content: str) -> str:
        draft_id = f"draft_{uuid.uuid4().hex}"
        self.storage.write_json(f"{self.root}/drafts/{draft_id}.json", {"schema_version": 1, "draft_id": draft_id, "run_id": run_id, "name": name, "content": content, "created_at": now_iso()})
        return draft_id

    def save_change_set(self, change_set: ChangeSet) -> None:
        self.storage.write_json(f"{self.root}/changes/{change_set.change_set_id}.json", asdict(change_set))

    def load_change_set(self, change_set_id: str) -> ChangeSet | None:
        data = self.storage.read_json(f"{self.root}/changes/{change_set_id}.json")
        if not isinstance(data, dict):
            return None
        data["operations"] = [ChangeOperation(**item) for item in data.get("operations", [])]
        return ChangeSet(**data)

    def list_pending_change_sets(self) -> list[ChangeSet]:
        result = []
        for path in self.storage.list_files(f"{self.root}/changes"):
            data = self.storage.read_json(path)
            if isinstance(data, dict) and data.get("status") == "pending":
                data["operations"] = [ChangeOperation(**item) for item in data.get("operations", [])]
                result.append(ChangeSet(**data))
        return sorted(result, key=lambda item: item.created_at, reverse=True)

    def save_skill(self, skill_id: str, text: str, scope: str = "book") -> None:
        self.storage.write_text(f"{self.root}/skills/{scope}/{skill_id}.md", text)

    def list_skill_texts(self, scope: str = "book") -> list[tuple[str, str]]:
        return [(path.rsplit("/", 1)[-1][:-3], self.storage.read_text(path, "") or "") for path in self.storage.list_files(f"{self.root}/skills/{scope}") if path.endswith(".md")]

    def _session_path(self, session_id: str) -> str:
        return f"{self.root}/sessions/{session_id}.json"

    def _run_path(self, run_id: str) -> str:
        return f"{self.root}/runs/{run_id}.json"
