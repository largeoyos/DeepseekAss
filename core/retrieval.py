from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Protocol


RETRIEVAL_SCHEMA_VERSION = 1


@dataclass
class RetrievedContext:
    source_type: str
    source_id: str
    content: str
    score: float
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class IndexReport:
    backend: str
    document_count: int
    embedded_count: int
    revision: int
    rebuilt_at: str
    fallback_reason: str = ""


class RetrievalBackend(Protocol):
    def search(
        self,
        book_title: str,
        query: str,
        filters: dict | None = None,
        limit: int | None = None,
    ) -> list[RetrievedContext]: ...

    def update_documents(self, book_title: str, changes: list[dict]) -> None: ...
    def rebuild(self, book_title: str) -> IndexReport: ...


class ClassicRetrievalBackend:
    backend_name = "classic"

    def __init__(self, novel_manager, settings: dict | None = None) -> None:
        self.manager = novel_manager
        self.settings = settings or {}

    def search(self, book_title: str, query: str, filters: dict | None = None, limit: int | None = None) -> list[RetrievedContext]:
        documents = _collect_documents(self.manager, book_title)
        filtered = _filter_documents(self.manager, book_title, documents, filters or {})
        scored = []
        min_score = self._min_score()
        for document in filtered:
            score = _keyword_score(query, document["content"], document["metadata"])
            pinned = document["metadata"].get("resident") or document["metadata"].get("manual")
            if pinned or (score > 0 and score >= min_score):
                scored.append(_to_result(document, score, "关键词、实体ID或上下文策略命中"))
        scored.sort(key=lambda item: (item.metadata.get("manual", False), item.metadata.get("resident", False), item.score), reverse=True)
        return scored[:self._result_limit(limit)]

    def _result_limit(self, limit: int | None = None) -> int:
        value = self.settings.get("retrieval_default_limit", 8) if limit is None else limit
        try:
            return max(1, min(50, int(value)))
        except (TypeError, ValueError):
            return 8

    def _min_score(self) -> float:
        try:
            return max(0.0, min(1.0, float(self.settings.get("retrieval_min_score", 0) or 0)))
        except (TypeError, ValueError):
            return 0.0

    def update_documents(self, book_title: str, changes: list[dict]) -> None:
        return

    def rebuild(self, book_title: str) -> IndexReport:
        count = len(_collect_documents(self.manager, book_title))
        return IndexReport("classic", count, 0, 0, _now())


class LlamaIndexHybridBackend(ClassicRetrievalBackend):
    backend_name = "hybrid"

    def __init__(self, novel_manager, settings: dict) -> None:
        super().__init__(novel_manager, settings)
        self._embedder = self._build_embedder(settings)

    @staticmethod
    def _build_embedder(settings: dict):
        try:
            from llama_index.embeddings.openai import OpenAIEmbedding
        except ModuleNotFoundError as exc:
            missing = getattr(exc, "name", "") or "llama-index-embeddings-openai"
            raise RuntimeError(
                f"当前 Python 环境缺少 Embedding 组件：{missing}。"
                "请在启动程序所用的环境中安装 llama-index-embeddings-openai。"
            ) from exc
        except Exception as exc:
            raise RuntimeError(f"LlamaIndex Embedding 组件加载失败：{exc}") from exc
        model = str(settings.get("embedding_model", "") or "").strip()
        if not model:
            raise RuntimeError("尚未配置 Embedding 模型")
        from config import Config
        base_url = str(settings.get("embedding_base_url", "") or Config.BASE_URL or "").strip()
        api_key = str(settings.get("embedding_api_key", "") or Config.API_KEY or "").strip()
        kwargs = {
            "model_name": model,
            "embed_batch_size": max(1, min(32, int(settings.get("embedding_batch_size", 8) or 8))),
            "timeout": float(settings.get("embedding_timeout_seconds", 20) or 20),
            "max_retries": max(0, min(3, int(settings.get("embedding_max_retries", 1) or 1))),
        }
        if base_url:
            kwargs["api_base"] = base_url.rstrip("/")
        if api_key:
            kwargs["api_key"] = api_key
        return OpenAIEmbedding(**kwargs)

    def search(self, book_title: str, query: str, filters: dict | None = None, limit: int | None = None) -> list[RetrievedContext]:
        try:
            documents = _collect_documents(self.manager, book_title)
            filtered = _filter_documents(self.manager, book_title, documents, filters or {})
            index = self._ensure_index(book_title, documents)
            query_vector = list(self._embedder.get_query_embedding(query))
        except Exception as exc:
            if not bool(self.settings.get("framework_auto_fallback", True)):
                raise
            self._last_fallback_reason = str(exc)
            return super().search(book_title, query, filters, limit)
        vector_by_checksum = index.get("vectors", {})
        candidates: dict[str, RetrievedContext] = {}
        keyword_weight, semantic_weight = self._hybrid_weights()
        min_score = self._min_score()
        for document in filtered:
            checksum = document["metadata"]["source_checksum"]
            vector = vector_by_checksum.get(checksum)
            semantic = _cosine(query_vector, vector) if isinstance(vector, list) else 0.0
            keyword = _keyword_score(query, document["content"], document["metadata"])
            score = keyword * keyword_weight + max(0.0, semantic) * semantic_weight
            pinned = document["metadata"].get("resident") or document["metadata"].get("manual")
            if not pinned and (score <= 0 or score < min_score):
                continue
            reason = f"混合检索：关键词={keyword:.3f}×{keyword_weight:.2f}，语义={semantic:.3f}×{semantic_weight:.2f}"
            result = _to_result(document, score, reason)
            candidates[f"{result.source_type}:{result.source_id}"] = result
        result = list(candidates.values())
        result.sort(key=lambda item: (item.metadata.get("manual", False), item.metadata.get("resident", False), item.score), reverse=True)
        return result[:self._result_limit(limit)]

    def _hybrid_weights(self) -> tuple[float, float]:
        try:
            keyword = max(0.0, float(self.settings.get("retrieval_keyword_weight", 55) or 0))
            semantic = max(0.0, float(self.settings.get("retrieval_semantic_weight", 45) or 0))
        except (TypeError, ValueError):
            keyword, semantic = 55.0, 45.0
        total = keyword + semantic
        if total <= 0:
            return 0.55, 0.45
        return keyword / total, semantic / total

    def update_documents(self, book_title: str, changes: list[dict]) -> None:
        workspace = self.manager.get_workspace(book_title)
        if not workspace.storage.exists(self._index_path(workspace)):
            return
        index = workspace.storage.read_json(self._index_path(workspace), default={}) or {}
        if not isinstance(index, dict):
            return
        index["dirty"] = True
        index["pending_changes"] = list(changes or [])[-100:]
        workspace.storage.write_json(self._index_path(workspace), index)

    def rebuild(self, book_title: str) -> IndexReport:
        documents = _collect_documents(self.manager, book_title)
        return self._write_index(book_title, documents, previous={})

    def clear(self, book_title: str) -> bool:
        workspace = self.manager.get_workspace(book_title)
        return workspace.storage.delete(self._index_path(workspace))

    def status(self, book_title: str) -> dict:
        workspace = self.manager.get_workspace(book_title)
        data = workspace.storage.read_json(self._index_path(workspace), default={}) or {}
        return data if isinstance(data, dict) else {}

    def _ensure_index(self, book_title: str, documents: list[dict]) -> dict:
        workspace = self.manager.get_workspace(book_title)
        previous = workspace.storage.read_json(self._index_path(workspace), default={}) or {}
        checksums = {item["metadata"]["source_checksum"] for item in documents}
        stored = set((previous.get("vectors") or {}).keys()) if isinstance(previous, dict) else set()
        if (
            not isinstance(previous, dict)
            or int(previous.get("schema_version", 0) or 0) != RETRIEVAL_SCHEMA_VERSION
            or previous.get("dirty")
            or checksums != stored
        ):
            report = self._write_index(
                book_title,
                documents,
                previous=previous if isinstance(previous, dict) else {},
            )
            previous = workspace.storage.read_json(self._index_path(workspace), default={}) or {}
            previous["last_report"] = asdict(report)
        return previous
    def _write_index(self, book_title: str, documents: list[dict], previous: dict) -> IndexReport:
        workspace = self.manager.get_workspace(book_title)
        old_vectors = dict(previous.get("vectors") or {})
        vectors: dict[str, list[float]] = {}
        pending: list[dict] = []
        for document in documents:
            checksum = document["metadata"]["source_checksum"]
            if checksum in old_vectors:
                vectors[checksum] = old_vectors[checksum]
            else:
                pending.append(document)
        if pending and hasattr(self._embedder, "get_text_embedding_batch"):
            batch_vectors = self._embedder.get_text_embedding_batch([item["content"] for item in pending])
            for document, vector in zip(pending, batch_vectors):
                vectors[document["metadata"]["source_checksum"]] = list(vector)
        else:
            for document in pending:
                vectors[document["metadata"]["source_checksum"]] = list(
                    self._embedder.get_text_embedding(document["content"])
                )
        embedded = len(pending)
        revision = int(previous.get("revision", 0) or 0) + 1
        payload = {
            "schema_version": RETRIEVAL_SCHEMA_VERSION,
            "revision": revision,
            "embedding_model": str(self.settings.get("embedding_model", "") or ""),
            "updated_at": _now(),
            "dirty": False,
            "vectors": vectors,
            "documents": [
                {
                    "source_type": item["source_type"],
                    "source_id": item["source_id"],
                    "source_checksum": item["metadata"]["source_checksum"],
                    "metadata": {key: value for key, value in item["metadata"].items() if key != "content"},
                }
                for item in documents
            ],
        }
        workspace.storage.write_json(self._index_path(workspace), payload)
        manifest = self.manager.ensure_workspace(book_title)
        manifest.features.update({
            "llama_index": True,
            "retrieval_schema_version": RETRIEVAL_SCHEMA_VERSION,
            "embedding_model": payload["embedding_model"],
            "index_revision": revision,
        })
        workspace.storage.write_json(workspace.manifest_path, asdict(manifest))
        return IndexReport("hybrid", len(documents), embedded, revision, payload["updated_at"])

    @staticmethod
    def _index_path(workspace) -> str:
        return f"{workspace.agent_root}/retrieval/index.json"


def build_retrieval_backend(novel_manager, settings: dict) -> tuple[RetrievalBackend, str]:
    requested = str(settings.get("retrieval_backend", "classic") or "classic")
    if requested != "hybrid":
        return ClassicRetrievalBackend(novel_manager, settings), ""
    try:
        return LlamaIndexHybridBackend(novel_manager, settings), ""
    except Exception as exc:
        if not bool(settings.get("framework_auto_fallback", True)):
            raise
        return ClassicRetrievalBackend(novel_manager, settings), str(exc)


def _collect_documents(manager, book_title: str) -> list[dict]:
    documents: list[dict] = []
    manifest = manager.ensure_workspace(book_title)
    meta = manager.ensure_chapter_tree(book_title)
    book_id = manifest.book_id
    for node in meta.chapter_nodes.values():
        if node.get("virtual"):
            continue
        content = manager.read_chapter_node(book_title, str(node.get("id", ""))) or ""
        base = {
            "book_id": book_id,
            "chapter_node_id": str(node.get("id", "")),
            "tree_id": str(node.get("tree_id", "primary_tree")),
            "chapter_num": int(node.get("chapter_num", 0) or 0),
            "scope": "branch",
            "updated_at": str(node.get("updated_at", "")),
        }
        if content.strip():
            documents.append(_document("chapter", str(node["id"]), content, base))
        summary = str(node.get("summary", "") or "").strip()
        if summary:
            documents.append(_document("chapter_summary", str(node["id"]), summary, base))
    book_meta = manager.load_meta(book_title)
    author_plan = str(getattr(book_meta, "author_plan", "") or "").strip()
    if author_plan:
        documents.append(_document("author_plan", "author_plan", author_plan, {"book_id": book_id, "scope": "global"}))
    compressed = str(getattr(book_meta, "compressed_early_summary", "") or "").strip()
    if compressed:
        documents.append(_document("compressed_memory", "compressed_early_summary", compressed, {"book_id": book_id, "scope": "global"}))
    try:
        from core.context_assembler import _entity_text, _world_entities

        bible = manager.load_world_bible(book_title)
        policies = manager.get_workspace(book_title).load_context_policies()
        for entity_id, kind, name, item in _world_entities(bible):
            policy = dict(policies.get(entity_id) or {})
            metadata = {
                "book_id": book_id,
                "entity_id": entity_id,
                "entity_kind": kind,
                "entity_name": name,
                "scope": str(getattr(item, "scope", "") or policy.get("scope") or "global"),
                "tree_id": str(getattr(item, "tree_id", "") or policy.get("tree_id") or ""),
                "anchor_node_id": str(getattr(item, "anchor_node_id", "") or policy.get("anchor_node_id") or ""),
                "resident": policy.get("load_mode") == "resident",
            }
            documents.append(_document("world_entity", entity_id, _entity_text(kind, item), metadata))
    except Exception:
        pass
    return documents


def _filter_documents(manager, book_title: str, documents: list[dict], filters: dict) -> list[dict]:
    meta = manager.ensure_chapter_tree(book_title)
    active_tree = str(filters.get("tree_id") or meta.active_tree_id or "primary_tree")
    active_path = set(filters.get("active_path") or meta.active_path)
    manual_ids = {str(item) for item in filters.get("manual_entity_ids", [])}
    result = []
    for document in documents:
        metadata = document["metadata"]
        if metadata.get("entity_id") in manual_ids:
            metadata["manual"] = True
            result.append(document)
            continue
        source_type = document["source_type"]
        tree_id = str(metadata.get("tree_id", "") or "")
        if source_type in {"chapter", "chapter_summary"}:
            if tree_id != active_tree or metadata.get("chapter_node_id") not in active_path:
                continue
        scope = str(metadata.get("scope", "global") or "global")
        if scope in {"branch", "chapter"}:
            anchor = str(metadata.get("anchor_node_id", "") or "")
            if tree_id and tree_id != active_tree:
                continue
            if anchor and anchor not in active_path:
                continue
        result.append(document)
    return result


def _document(source_type: str, source_id: str, content: str, metadata: dict) -> dict:
    clean = str(content or "").strip()
    meta = dict(metadata)
    meta.update({
        "source_type": source_type,
        "source_id": source_id,
        "source_checksum": hashlib.sha256(clean.encode("utf-8")).hexdigest(),
        "updated_at": str(meta.get("updated_at") or _now()),
    })
    return {"source_type": source_type, "source_id": source_id, "content": clean, "metadata": meta}


def _to_result(document: dict, score: float, reason: str) -> RetrievedContext:
    return RetrievedContext(
        document["source_type"],
        document["source_id"],
        document["content"],
        float(score),
        reason,
        dict(document["metadata"]),
    )


def _keyword_score(query: str, content: str, metadata: dict) -> float:
    query_tokens = _tokens(query)
    if not query_tokens:
        return 0.0
    haystack = "\n".join([content, json.dumps(metadata, ensure_ascii=False)]).lower()
    matches = sum(1 for token in query_tokens if token in haystack)
    return matches / max(1, len(query_tokens))


def _tokens(text: str) -> set[str]:
    normalized = re.sub(r"\s+", "", str(text or "").lower())
    words = set(re.findall(r"[a-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", normalized))
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", normalized))
    words.update(chinese[index:index + 2] for index in range(max(0, len(chinese) - 1)))
    return {item for item in words if item}


def _cosine(left: list[float], right: list[float] | None) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = math.sqrt(sum(value * value for value in left))
    norm_right = math.sqrt(sum(value * value for value in right))
    return dot / (norm_left * norm_right) if norm_left and norm_right else 0.0


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
