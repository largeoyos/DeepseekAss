"""Persistent output view; the Python transcript remains authoritative on close."""
import html
import json

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWebEngineWidgets import QWebEngineView


class DisplayHistoryView(QWebEngineView):
    save_error = pyqtSignal(str)

    def __init__(self, store, style_provider, navigation_script, parent=None):
        super().__init__(parent)
        self.store = store
        self.style_provider = style_provider
        self.navigation_script = navigation_script
        self._ready = False
        self._callbacks = []
        self._welcome = ""
        self.loadFinished.connect(self._on_loaded)
        self._save_timer = QTimer(self)
        self._save_timer.setInterval(2000)
        self._save_timer.timeout.connect(self._autosave)
        self._save_timer.start()
        super().setHtml(f"<html><head>{style_provider()}</head><body></body></html>")

    @staticmethod
    def entry_html(entry):
        kind = entry.get("kind", "assistant")
        css = {"assistant": "assistant-msg", "user": "user-msg", "system": "system-msg",
               "behavior": "sender-behavior-msg"}.get(kind, "assistant-msg")
        return f'<div id="display-{html.escape(entry["id"], quote=True)}" class="{css}">{entry["html"]}</div>'

    def _on_loaded(self, ok):
        if not ok:
            return
        self._ready = True
        body = "".join(self.entry_html(entry) for entry in self.store.entries)
        body_expression = json.dumps(body) if body else f"new DOMParser().parseFromString({json.dumps(self._welcome)}, 'text/html').body.innerHTML"
        script = f"document.head.innerHTML = {json.dumps(self.style_provider())}; document.body.innerHTML = {body_expression}; {self.navigation_script} rebuildReplyJumpNav(); window.scrollTo(0, document.body.scrollHeight);"
        self.page().runJavaScript(script, self._finish_initial_render)

    def _finish_initial_render(self, _result):
        callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            callback(True)

    def setHtml(self, content, *args):
        # Existing welcome/reset calls must not discard a transcript. Actual
        # messages enter through append_block/update_reply/merge_blocks.
        self.store.begin_reply()
        self._welcome = content
        if self._ready:
            self.refresh_style()
            if not self.store.entries:
                script = "document.body.innerHTML = new DOMParser().parseFromString(" + json.dumps(content) + ", 'text/html').body.innerHTML;"
                self.page().runJavaScript(script)

    def refresh_style(self):
        if self._ready:
            self.page().runJavaScript("document.head.innerHTML = " + json.dumps(self.style_provider()) + ";")

    def _show_entry(self, entry, callback=None):
        if not self._ready:
            if callback:
                self._callbacks.append(callback)
            return
        body = self.entry_html(entry)
        script = f"""(() => {{
            const followBottom = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 48;
            let old = document.getElementById({json.dumps('display-' + entry['id'])});
            if (old) {{
                // Preserve the message element: replacing it restarts fadeIn
                // on every streamed chunk and invalidates reply navigation.
                if (old.innerHTML !== {json.dumps(entry['html'])})
                    old.innerHTML = {json.dumps(entry['html'])};
            }} else {{
                document.body.insertAdjacentHTML('beforeend', {json.dumps(body)});
                {self.navigation_script}
                rebuildReplyJumpNav();
            }}
            if (followBottom) window.scrollTo(0, document.body.scrollHeight);
            return true;
        }})()"""
        self.page().runJavaScript(script, callback or (lambda _: None))

    def append_block(self, kind, body, source_id=""):
        entry = self.store.append(kind, body, source_id)
        self._show_entry(entry)

    def begin_reply(self):
        self.store.begin_reply()

    def update_reply(self, body, callback=None):
        self._show_entry(self.store.update_reply(body), callback)

    def merge_blocks(self, blocks, callback=None):
        for kind, body, source_id in blocks:
            self.append_block(kind, body, source_id)
        if callback:
            if self._ready:
                self.page().runJavaScript("true", callback)
            else:
                self._callbacks.append(callback)

    def _autosave(self):
        try:
            self.store.save()
        except Exception as exc:
            self.save_error.emit(str(exc))
