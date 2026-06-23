from __future__ import annotations

import ipaddress
import json
import socket
import ssl
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


class WebSearchError(RuntimeError):
    pass


@dataclass
class WebSearchConfig:
    enabled: bool = False
    endpoint: str = ""
    method: str = "POST"
    api_key: str = ""
    auth_header: str = "Authorization"
    auth_prefix: str = "Bearer"
    query_field: str = "query"
    results_path: str = "results"
    title_field: str = "title"
    url_field: str = "url"
    snippet_field: str = "snippet"
    max_results: int = 5
    timeout_seconds: float = 15.0

    @classmethod
    def from_settings(cls, settings: dict | None) -> "WebSearchConfig":
        settings = settings or {}
        return cls(
            enabled=bool(settings.get("agent_web_enabled") or settings.get("web_search_enabled")),
            endpoint=str(settings.get("agent_web_endpoint") or settings.get("web_search_endpoint", "") or "").strip(),
            method=str(settings.get("agent_web_method") or settings.get("web_search_method", "POST") or "POST").upper(),
            api_key=str(settings.get("agent_web_api_key") or settings.get("web_search_api_key", "") or ""),
            auth_header=str(settings.get("agent_web_auth_header") or settings.get("web_search_auth_header", "Authorization") or "Authorization"),
            auth_prefix=str(settings.get("agent_web_auth_prefix") or settings.get("web_search_auth_prefix", "Bearer") or "Bearer"),
            query_field=str(settings.get("agent_web_query_field") or settings.get("web_search_query_field", "query") or "query"),
            results_path=str(settings.get("agent_web_results_path") or settings.get("web_search_results_path", "results") or "results"),
            title_field=str(settings.get("agent_web_title_field") or settings.get("web_search_title_field", "title") or "title"),
            url_field=str(settings.get("agent_web_url_field") or settings.get("web_search_url_field", "url") or "url"),
            snippet_field=str(settings.get("agent_web_snippet_field") or settings.get("web_search_snippet_field", "snippet") or "snippet"),
            max_results=max(1, min(10, int(settings.get("agent_web_max_results") or settings.get("web_search_max_results", 5) or 5))),
            timeout_seconds=max(1.0, min(30.0, float(settings.get("agent_web_timeout_seconds") or 15))),
        )

    def is_available(self) -> bool:
        return self.enabled and bool(self.endpoint)


class WebSearchClient:
    def __init__(self, config: WebSearchConfig) -> None:
        self.config = config

    def search(self, query: str, max_results: int | None = None) -> dict:
        query = str(query or "").strip()
        if not query:
            raise WebSearchError("搜索词不能为空")
        if not self.config.is_available():
            raise WebSearchError("网页搜索未启用或未配置 endpoint")
        self._validate_endpoint(self.config.endpoint)
        limit = max(1, min(10, int(max_results or self.config.max_results)))
        data = self._request(query, limit)
        raw_results = _path_get(data, self.config.results_path)
        if isinstance(raw_results, dict):
            raw_results = raw_results.get("items") or raw_results.get("results") or []
        if not isinstance(raw_results, list):
            raw_results = []
        results = []
        for item in raw_results[:limit]:
            if not isinstance(item, dict):
                continue
            url = str(_path_get(item, self.config.url_field) or "").strip()
            if url and not url.lower().startswith(("http://", "https://")):
                url = ""
            results.append({
                "title": str(_path_get(item, self.config.title_field) or "").strip(),
                "url": url,
                "snippet": str(_path_get(item, self.config.snippet_field) or "").strip(),
            })
        return {"query": query, "searched_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "results": results}

    def _request(self, query: str, limit: int) -> Any:
        method = self.config.method if self.config.method in {"GET", "POST"} else "POST"
        headers = {"Accept": "application/json"}
        if self.config.api_key:
            prefix = (self.config.auth_prefix or "").strip()
            value = f"{prefix} {self.config.api_key}".strip()
            headers[self.config.auth_header or "Authorization"] = value
        endpoint = self.config.endpoint
        body = None
        if method == "GET":
            url = urllib.parse.urlsplit(endpoint)
            params = dict(urllib.parse.parse_qsl(url.query, keep_blank_values=True))
            params[self.config.query_field] = query
            params.setdefault("max_results", str(limit))
            endpoint = urllib.parse.urlunsplit((url.scheme, url.netloc, url.path, urllib.parse.urlencode(params), url.fragment))
        else:
            headers["Content-Type"] = "application/json"
            body = json.dumps({self.config.query_field: query, "max_results": limit}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(endpoint, data=body, headers=headers, method=method)
        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl.create_default_context()), NoRedirectHandler())
        with opener.open(request, timeout=self.config.timeout_seconds) as response:
            final_url = response.geturl()
            self._validate_endpoint(final_url)
            payload = response.read(2_000_000).decode("utf-8", errors="replace")
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise WebSearchError("搜索 API 未返回 JSON") from exc

    @staticmethod
    def _validate_endpoint(endpoint: str) -> None:
        parsed = urllib.parse.urlparse(endpoint)
        if parsed.scheme.lower() != "https":
            raise WebSearchError("网页搜索 endpoint 只允许 HTTPS")
        host = parsed.hostname or ""
        if not host or host.lower() in {"localhost"} or host.endswith(".local"):
            raise WebSearchError("网页搜索 endpoint 不允许 localhost 或本地域名")
        try:
            ips = [ipaddress.ip_address(host)]
        except ValueError:
            try:
                infos = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
                ips = [ipaddress.ip_address(info[4][0]) for info in infos]
            except OSError as exc:
                raise WebSearchError(f"无法解析搜索 endpoint: {host}") from exc
        for ip in ips:
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                raise WebSearchError("网页搜索 endpoint 解析到私网或保留地址")


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802
        WebSearchClient._validate_endpoint(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _path_get(value: Any, path: str) -> Any:
    current = value
    for part in str(path or "").split("."):
        if part == "":
            continue
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current
