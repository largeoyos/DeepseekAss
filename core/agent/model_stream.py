"""Collect a streamed model turn while forwarding display text immediately."""
import json
from types import SimpleNamespace as NS


def collect_chat_stream(client, kwargs, on_text, check_cancel=None):
    def value(obj, key, default=None):
        return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)

    if check_cancel:
        check_cancel()
    stream = client.chat.completions.create(**kwargs, stream=True)
    parts, calls = [], {}
    usage, final, finish = None, None, ""
    try:
        for chunk in stream:
            if check_cancel:
                check_cancel()
            usage = value(chunk, "usage") or usage
            final = value(chunk, "gateway_result") or final
            choices = value(chunk, "choices", []) or []
            if not choices:
                continue
            choice = choices[0]
            finish = value(choice, "finish_reason") or finish
            delta = value(choice, "delta", {}) or {}
            text = value(delta, "content") or ""
            if text:
                parts.append(text)
                on_text(text)
            for call in value(delta, "tool_calls", []) or []:
                index = value(call, "index", 0)
                entry = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                entry["id"] += value(call, "id") or ""
                function = value(call, "function", {}) or {}
                entry["name"] += value(function, "name") or ""
                entry["arguments"] += value(function, "arguments") or ""
    finally:
        close = getattr(stream, "close", None)
        if callable(close):
            close()
    if check_cancel:
        check_cancel()
    if final is not None:
        # The gateway validates tool names/arguments before returning this turn.
        content = final.content
        tool_calls = [NS(id=call.call_id, function=NS(name=call.name,
                      arguments=json.dumps(call.arguments, ensure_ascii=False))) for call in final.tool_calls]
        finish = final.finish_reason
        if content and not parts:
            on_text(content)
    else:
        content = "".join(parts)
        tool_calls = [NS(id=item["id"], function=NS(name=item["name"], arguments=item["arguments"]))
                      for _, item in sorted(calls.items())]
    return NS(choices=[NS(message=NS(content=content, tool_calls=tool_calls), finish_reason=finish)],
              usage=usage, model=kwargs.get("model", ""))
