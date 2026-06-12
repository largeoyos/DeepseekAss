import threading

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QDialog,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.novel_manager import NovelManager
from ui.chapter_editor_dialog import ChapterEditorDialog
from utils.prompts import Prompts
from utils.supplement import count_cn


class ChapterNodeItem(QGraphicsRectItem):
    """Clickable chapter node used by the layered graph view."""

    def __init__(
        self,
        dialog: "ChapterTreeDialog",
        node: dict,
        x: float,
        y: float,
        width: float,
        height: float,
        *,
        active: bool,
        selected: bool,
    ):
        super().__init__(0, 0, width, height)
        self._dialog = dialog
        self._node = node
        self.setPos(x, y)
        self.setAcceptHoverEvents(True)
        self._apply_style(active=active, selected=selected)

        title = node.get("title") or f"第{node.get('chapter_num')}章"
        text = QGraphicsTextItem(
            f"第{node.get('chapter_num')}章  v{node.get('version')}\n{title}",
            self,
        )
        text.setDefaultTextColor(QColor("#ffffff" if active or selected else "#d8dde8"))
        text.setTextWidth(width - 16)
        text.setPos(8, 7)

    def _apply_style(self, *, active: bool, selected: bool) -> None:
        if selected:
            fill, border, width = "#254f78", "#86c7ff", 2
        elif active:
            fill, border, width = "#1e3a5f", "#4fc1ff", 2
        else:
            fill, border, width = "#2a2a3e", "#596070", 1
        self.setBrush(QBrush(QColor(fill)))
        self.setPen(QPen(QColor(border), width))

    def mousePressEvent(self, event) -> None:
        self._dialog._select_node(self._node["id"])
        super().mousePressEvent(event)


class ChapterTreeDialog(QDialog):
    """Tree-based chapter manager backed by NovelManager's compatible metadata."""

    generation_done = pyqtSignal(str)
    generation_failed = pyqtSignal(str)

    def __init__(self, parent, novel_manager: NovelManager, book_title: str, client=None):
        super().__init__(parent)
        self._novel_manager = novel_manager
        self._book_title = book_title
        self._client = client
        self._meta = None
        self._current_node: dict | None = None
        self._selected_node_id: str | None = None
        self.setWindowTitle(f"章节树管理 - {book_title}")
        self.resize(980, 640)
        self.generation_done.connect(self._on_generation_done)
        self.generation_failed.connect(self._on_generation_failed)
        self._init_ui()
        self._load_tree()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        hint = QLabel("章节图形树（从上到下为父子层级；高亮为活跃路径）")
        hint.setWordWrap(True)
        left_layout.addWidget(hint)
        self._scene = QGraphicsScene(self)
        self._graph = QGraphicsView(self._scene)
        self._graph.setRenderHints(self._graph.renderHints())
        self._graph.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        left_layout.addWidget(self._graph, stretch=1)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        self._path_label = QLabel("")
        self._path_label.setWordWrap(True)
        right_layout.addWidget(self._path_label)

        self._details = QTextEdit()
        self._details.setReadOnly(True)
        right_layout.addWidget(self._details, stretch=1)

        button_row = QHBoxLayout()
        switch_btn = QPushButton("切换分支")
        switch_btn.clicked.connect(self._switch_branch)
        edit_btn = QPushButton("编辑正文")
        edit_btn.clicked.connect(self._edit_current)
        polish_btn = QPushButton("润色")
        polish_btn.clicked.connect(lambda: self._generate_variant("polish"))
        rewrite_btn = QPushButton("重写")
        rewrite_btn.clicked.connect(lambda: self._generate_variant("rewrite"))
        insert_btn = QPushButton("插入中间章")
        insert_btn.clicked.connect(self._insert_middle_chapter)
        delete_btn = QPushButton("删除子树")
        delete_btn.clicked.connect(self._delete_current)
        for btn in (switch_btn, edit_btn, polish_btn, rewrite_btn, insert_btn, delete_btn):
            button_row.addWidget(btn)
        right_layout.addLayout(button_row)
        splitter.addWidget(right)

        splitter.setSizes([360, 620])
        layout.addWidget(splitter, stretch=1)

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

    def _load_tree(self) -> None:
        self._meta = self._novel_manager.ensure_chapter_tree(self._book_title)
        self._scene.clear()
        nodes = self._meta.chapter_nodes
        active = set(self._meta.active_path)
        by_parent: dict[str | None, list[dict]] = {}
        for node in nodes.values():
            by_parent.setdefault(node.get("parent_id"), []).append(node)
        for siblings in by_parent.values():
            siblings.sort(key=lambda n: (int(n.get("chapter_num", 0)), int(n.get("sibling_order", 0))))

        roots = by_parent.get(None, [])
        if not roots and self._meta.root_chapter_id in nodes:
            roots = [nodes[self._meta.root_chapter_id]]
        if self._selected_node_id not in nodes:
            self._selected_node_id = self._meta.active_path[-1] if self._meta.active_path else (roots[0]["id"] if roots else None)
        self._draw_layered_tree(roots, by_parent, active)
        self._update_path_label()
        self._on_tree_selection()

    def _draw_layered_tree(
        self,
        roots: list[dict],
        by_parent: dict[str | None, list[dict]],
        active: set[str],
    ) -> None:
        levels: list[list[dict]] = []
        queue = [(node, 0) for node in roots]
        while queue:
            node, depth = queue.pop(0)
            while len(levels) <= depth:
                levels.append([])
            levels[depth].append(node)
            for child in by_parent.get(node["id"], []):
                queue.append((child, depth + 1))

        node_w, node_h = 190, 64
        x_gap, y_gap = 36, 72
        positions: dict[str, tuple[float, float]] = {}
        scene_width = max(1, max((len(level) for level in levels), default=1)) * (node_w + x_gap)

        for depth, level in enumerate(levels):
            row_width = len(level) * node_w + max(0, len(level) - 1) * x_gap
            start_x = max(20, (scene_width - row_width) / 2)
            y = 24 + depth * (node_h + y_gap)
            for idx, node in enumerate(level):
                x = start_x + idx * (node_w + x_gap)
                positions[node["id"]] = (x, y)

        for parent_id, children in by_parent.items():
            if not parent_id or parent_id not in positions:
                continue
            px, py = positions[parent_id]
            for child in children:
                if child["id"] not in positions:
                    continue
                cx, cy = positions[child["id"]]
                path = QPainterPath()
                path.moveTo(px + node_w / 2, py + node_h)
                mid_y = py + node_h + y_gap / 2
                path.cubicTo(px + node_w / 2, mid_y, cx + node_w / 2, mid_y, cx + node_w / 2, cy)
                edge = QGraphicsPathItem(path)
                edge.setPen(QPen(QColor("#4fc1ff" if child["id"] in active and parent_id in active else "#596070"), 2))
                edge.setZValue(-1)
                self._scene.addItem(edge)

        for node_id, (x, y) in positions.items():
            node = self._meta.chapter_nodes[node_id]
            self._scene.addItem(
                ChapterNodeItem(
                    self,
                    node,
                    x,
                    y,
                    node_w,
                    node_h,
                    active=node_id in active,
                    selected=node_id == self._selected_node_id,
                )
            )
        self._scene.setSceneRect(self._scene.itemsBoundingRect().adjusted(-24, -24, 24, 24))
        self._graph.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def _update_path_label(self) -> None:
        if not self._meta:
            return
        parts = []
        for node_id in self._meta.active_path:
            node = self._meta.chapter_nodes.get(node_id)
            if node:
                parts.append(f"第{node.get('chapter_num')}章 v{node.get('version')}")
        self._path_label.setText("活跃路径: " + (" → ".join(parts) if parts else "未设置"))

    def _selected_node(self) -> dict | None:
        if not self._selected_node_id or not self._meta:
            return None
        return self._meta.chapter_nodes.get(self._selected_node_id)

    def _select_node(self, node_id: str) -> None:
        self._selected_node_id = node_id
        self._load_tree()

    def _on_tree_selection(self, current=None, previous=None) -> None:
        self._current_node = self._selected_node()
        if not self._current_node:
            self._details.setPlainText("请选择章节节点。")
            return
        node = self._current_node
        content = self._novel_manager.read_chapter_node(self._book_title, node["id"]) or ""
        record = self._novel_manager.load_generation_record(
            self._book_title, int(node["chapter_num"]), int(node["version"])
        ) or {}
        details = [
            f"章节: 第{node.get('chapter_num')}章",
            f"标题: {node.get('title', '')}",
            f"版本: v{node.get('version')}",
            f"节点: {node.get('id')}",
            f"父节点: {node.get('parent_id') or '(无)'}",
            f"子节点: {len(node.get('children_ids', []))}",
            f"创建: {node.get('created_at', '')}",
            f"中文字数: {count_cn(content)}",
            "",
            "生成参数:",
            f"  模型: {record.get('model', '')}",
            f"  temperature: {record.get('temperature', '')}",
            f"  top_p: {record.get('top_p', '')}",
            f"  max_tokens: {record.get('max_tokens', '')}",
            "",
            "生成提示词:",
            record.get("prompt", "")[:3000] or "(无记录)",
            "",
            "正文预览:",
            content[:2000] or "(空)",
        ]
        self._details.setPlainText("\n".join(details))

    def _switch_branch(self) -> None:
        node = self._selected_node()
        if not node:
            return
        if self._novel_manager.switch_active_node(self._book_title, node["id"]):
            self._load_tree()
            QMessageBox.information(self, "完成", "已切换活跃路径。建议按需重建剧情摘要。")

    def _edit_current(self) -> None:
        node = self._selected_node()
        if not node:
            return
        dialog = ChapterEditorDialog(self, self._novel_manager, self._book_title, node)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._load_tree()

    def _generate_variant(self, mode: str) -> None:
        node = self._selected_node()
        if not node or not self._client:
            return
        label = "润色要求" if mode == "polish" else "重写要求"
        requirement, ok = QInputDialog.getMultiLineText(self, label, f"请输入{label}：")
        if not ok or not requirement.strip():
            return
        self._details.setPlainText("正在生成新版本，请稍候...")
        threading.Thread(target=self._run_generation, args=(node, mode, requirement.strip()), daemon=True).start()

    def _run_generation(self, node: dict, mode: str, requirement: str) -> None:
        try:
            old_content = self._novel_manager.read_chapter_node(self._book_title, node["id"]) or ""
            chapter_num = int(node["chapter_num"])
            title = node.get("title") or f"第{chapter_num}章"
            messages = [{"role": "system", "content": Prompts.NOVEL_CHAPTER_WRITING}]
            if mode == "polish":
                user_prompt = (
                    f"请基于以下章节全文进行润色，保留核心剧情，不要输出解释。\n\n"
                    f"【润色要求】\n{requirement}\n\n【原章节】\n{old_content}"
                )
            else:
                summary = self._novel_manager.load_smart_summary(
                    self._book_title,
                    client=self._client.raw_client,
                    next_chapter_num=chapter_num,
                    model=self._client.model,
                    global_user_prompt=self._client.global_user_prompt,
                )
                user_prompt = (
                    f"请重写第 {chapter_num} 章「{title}」，不要输出解释。\n\n"
                    f"【前情提要】\n{summary}\n\n【重写要求】\n{requirement}\n\n"
                    f"【旧版本参考】\n{old_content[:4000]}"
                )
            messages.append({"role": "user", "content": user_prompt})
            response = self._client.raw_client.chat.completions.create(
                model=self._client.model,
                messages=messages,
                temperature=self._client.temperature,
                top_p=self._client.top_p,
                max_tokens=self._client.max_tokens,
                frequency_penalty=self._client.frequency_penalty,
                stream=False,
            )
            content = response.choices[0].message.content or ""
            parent = self.parent()
            if hasattr(parent, "_log_token_usage"):
                parent._log_token_usage(
                    operation=f"chapter_tree_{mode}",
                    direction="send",
                    content=user_prompt,
                    usage=getattr(response, "usage", None),
                )
                parent._log_token_usage(
                    operation=f"chapter_tree_{mode}",
                    direction="receive",
                    content=content,
                    usage=getattr(response, "usage", None),
                )
            version = self._novel_manager.get_next_version(self._book_title, chapter_num)
            self._novel_manager.save_chapter_version(self._book_title, chapter_num, title, content, version=version)
            self._novel_manager.save_generation_record(
                self._book_title,
                chapter_num,
                title,
                version,
                user_prompt,
                self._client.model,
                self._client.temperature,
                self._client.top_p,
                self._client.max_tokens,
                self._client.frequency_penalty,
                content[:500],
                requirement=requirement,
            )
            self.generation_done.emit(f"已生成新版本 v{version}。")
        except Exception as exc:
            self.generation_failed.emit(str(exc))

    def _on_generation_done(self, message: str) -> None:
        QMessageBox.information(self, "完成", message)
        self._load_tree()

    def _on_generation_failed(self, message: str) -> None:
        QMessageBox.critical(self, "生成失败", message)
        self._load_tree()

    def _insert_middle_chapter(self) -> None:
        QMessageBox.information(
            self,
            "暂不执行",
            "插入中间章需要重排后续章节编号。为避免破坏旧书结构，请先通过生成/重写创建分支版本。",
        )

    def _delete_current(self) -> None:
        node = self._selected_node()
        if not node:
            return
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定删除节点 {node['id']} 及其所有子节点？此操作不可恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._novel_manager.delete_chapter_node(self._book_title, node["id"])
        self._load_tree()
