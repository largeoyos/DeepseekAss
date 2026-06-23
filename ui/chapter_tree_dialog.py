import re
import threading
import time

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QFileDialog,
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
        label = node.get("display_label") or f"第{node.get('chapter_num')}章  v{node.get('version')}"
        node_text = "第零章\n故事起点" if node.get("virtual") else f"{label}\n{title}"
        self._text_item = QGraphicsTextItem(node_text, self)
        self._text_item.setTextWidth(width - 16)
        self._text_item.setPos(8, 7)
        self._sync_text_color(active=active, selected=selected)

    def _apply_style(self, *, active: bool, selected: bool) -> None:
        if selected:
            fill, border, width = "#254f78", "#86c7ff", 2
        elif self._node.get("virtual"):
            fill, border, width = "#3b3347", "#b99ad6", 2
        elif active:
            fill, border, width = "#1e3a5f", "#4fc1ff", 2
        else:
            fill, border, width = "#2a2a3e", "#596070", 1
        self.setBrush(QBrush(QColor(fill)))
        self.setPen(QPen(QColor(border), width))
        if hasattr(self, "_text_item"):
            self._sync_text_color(active=active, selected=selected)

    def _sync_text_color(self, *, active: bool, selected: bool) -> None:
        self._text_item.setDefaultTextColor(QColor("#ffffff" if active or selected else "#d8dde8"))

    def mousePressEvent(self, event) -> None:
        self._dialog._select_node(self._node["id"])
        event.accept()


class ZoomableGraphicsView(QGraphicsView):
    """Graphics view with Ctrl+wheel zoom routed through the dialog."""

    def __init__(self, scene: QGraphicsScene, dialog: "ChapterTreeDialog"):
        super().__init__(scene)
        self._dialog = dialog

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self._dialog._zoom_in()
            elif delta < 0:
                self._dialog._zoom_out()
            event.accept()
            return
        super().wheelEvent(event)


class ChapterTreeDialog(QDialog):
    """Tree-based chapter manager backed by NovelManager's compatible metadata."""

    generation_done = pyqtSignal(str)
    generation_failed = pyqtSignal(str)
    rebuild_done = pyqtSignal(object)
    rebuild_failed = pyqtSignal(str)
    summary_done = pyqtSignal(str)
    summary_failed = pyqtSignal(str)
    polish_plan_ready = pyqtSignal(object, object, object)
    polish_plan_failed = pyqtSignal(str)
    extra_plan_ready = pyqtSignal(object, object)
    extra_plan_failed = pyqtSignal(str)

    def __init__(
        self, parent, novel_manager: NovelManager, book_title: str, client=None,
        novel_generation_mode: str = "classic",
        skills_enabled: bool = True,
    ):
        super().__init__(parent)
        self._novel_manager = novel_manager
        self._book_title = book_title
        self._client = client
        self._novel_generation_mode = (
            novel_generation_mode if novel_generation_mode in {"classic", "agent"} else "classic"
        )
        self._skills_enabled = bool(skills_enabled)
        self._meta = None
        self._current_node: dict | None = None
        self._selected_node_id: str | None = None
        self._node_items: dict[str, ChapterNodeItem] = {}
        self._zoom_factor = 1.0
        self._rebuild_success_message = "剧情记忆和世界书已按活跃路径同步。"
        self.setWindowTitle(f"章节树管理 - {book_title}")
        self.resize(980, 640)
        self.generation_done.connect(self._on_generation_done)
        self.generation_failed.connect(self._on_generation_failed)
        self.rebuild_done.connect(self._on_rebuild_done)
        self.rebuild_failed.connect(self._on_rebuild_failed)
        self.summary_done.connect(self._on_summary_done)
        self.summary_failed.connect(self._on_summary_failed)
        self.polish_plan_ready.connect(self._on_polish_plan_ready)
        self.polish_plan_failed.connect(self._on_polish_plan_failed)
        self.extra_plan_ready.connect(self._on_extra_plan_ready)
        self.extra_plan_failed.connect(self._on_extra_plan_failed)
        self._init_ui()
        self._load_tree()

    def _api_client(self, operation: str):
        parent = self.parent()
        if hasattr(parent, "_usage_logged_client"):
            return parent._usage_logged_client(operation)
        return self._client.raw_client if self._client else None

    def _finish_host_stream(self) -> None:
        parent = self.parent()
        if hasattr(parent, "_stream_signals"):
            parent._stream_signals.finished.emit()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        hint = QLabel("章节图形树（从上到下为父子层级；高亮为活跃路径）")
        hint.setWordWrap(True)
        left_layout.addWidget(hint)

        tree_row = QHBoxLayout()
        tree_row.addWidget(QLabel("阅读树"))
        self._tree_combo = QComboBox()
        self._tree_combo.currentIndexChanged.connect(self._on_tree_combo_changed)
        tree_row.addWidget(self._tree_combo, 1)
        left_layout.addLayout(tree_row)

        zoom_row = QHBoxLayout()
        zoom_out_btn = QPushButton("-")
        zoom_out_btn.setToolTip("缩小章节树")
        zoom_out_btn.clicked.connect(self._zoom_out)
        zoom_reset_btn = QPushButton("100%")
        zoom_reset_btn.setToolTip("恢复默认缩放")
        zoom_reset_btn.clicked.connect(self._reset_zoom)
        zoom_in_btn = QPushButton("+")
        zoom_in_btn.setToolTip("放大章节树")
        zoom_in_btn.clicked.connect(self._zoom_in)
        zoom_fit_btn = QPushButton("适应")
        zoom_fit_btn.setToolTip("适应当前窗口")
        zoom_fit_btn.clicked.connect(self._fit_zoom)
        self._zoom_label = QLabel("100%")
        self._zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for btn in (zoom_out_btn, zoom_reset_btn, zoom_in_btn, zoom_fit_btn):
            zoom_row.addWidget(btn)
        zoom_row.addWidget(self._zoom_label)
        zoom_row.addStretch()
        left_layout.addLayout(zoom_row)

        self._scene = QGraphicsScene(self)
        self._graph = ZoomableGraphicsView(self._scene, self)
        self._graph.setRenderHints(self._graph.renderHints())
        self._graph.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self._graph.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self._graph.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
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
        self._switch_btn = QPushButton("切换分支")
        self._switch_btn.clicked.connect(self._switch_branch)
        self._edit_btn = QPushButton("编辑正文")
        self._edit_btn.clicked.connect(self._edit_current)
        self._polish_btn = QPushButton(
            "Agent 润色" if self._novel_generation_mode == "agent" else "润色"
        )
        self._polish_btn.clicked.connect(lambda: self._generate_variant("polish"))
        self._rewrite_btn = QPushButton("重写")
        self._rewrite_btn.clicked.connect(lambda: self._generate_variant("rewrite"))
        self._export_btn = QPushButton("导出该章节")
        self._export_btn.clicked.connect(self._export_current)
        insert_btn = QPushButton("Agent 插入番外")
        insert_btn.clicked.connect(self._insert_agent_extra)
        self._extra_btn = insert_btn
        self._delete_btn = QPushButton("删除子树")
        self._delete_btn.clicked.connect(self._delete_current)
        self._summary_btn = QPushButton("重新生成摘要")
        self._summary_btn.clicked.connect(self._regenerate_summary)
        for btn in (
            self._switch_btn, self._edit_btn, self._polish_btn, self._rewrite_btn,
            self._export_btn, insert_btn, self._delete_btn, self._summary_btn,
        ):
            button_row.addWidget(btn)
        right_layout.addLayout(button_row)

        world_row = QHBoxLayout()
        current_wb_btn = QPushButton("提取当前章节世界书")
        current_wb_btn.setToolTip("只重新提取当前选中章节版本，然后按活跃路径重新合并世界书")
        current_wb_btn.clicked.connect(self._force_extract_current_world_bible)
        all_wb_btn = QPushButton("提取全部活跃路径世界书")
        all_wb_btn.setToolTip("依次读取当前活跃路径上的全部正文章节并重新提取世界书")
        all_wb_btn.clicked.connect(self._force_extract_all_world_bible)
        world_row.addWidget(current_wb_btn)
        world_row.addWidget(all_wb_btn)
        world_row.addStretch()
        right_layout.addLayout(world_row)
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
        self._node_items = {}
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
            item = ChapterNodeItem(
                self,
                node,
                x,
                y,
                node_w,
                node_h,
                active=node_id in active,
                selected=node_id == self._selected_node_id,
            )
            self._node_items[node_id] = item
            self._scene.addItem(item)
        self._scene.setSceneRect(self._scene.itemsBoundingRect().adjusted(-24, -24, 24, 24))
        self._apply_graph_zoom(fit=True)

    def _apply_graph_zoom(self, *, fit: bool = False) -> None:
        rect = self._scene.sceneRect()
        if rect.isNull() or rect.isEmpty():
            return
        if fit:
            self._graph.resetTransform()
            self._graph.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        if self._zoom_factor != 1.0:
            self._graph.scale(self._zoom_factor, self._zoom_factor)
        if hasattr(self, "_zoom_label"):
            self._zoom_label.setText(f"{int(self._zoom_factor * 100)}%")

    def _set_zoom_factor(self, factor: float) -> None:
        next_factor = max(0.35, min(3.0, factor))
        if next_factor == self._zoom_factor:
            return
        ratio = next_factor / self._zoom_factor
        self._zoom_factor = next_factor
        self._graph.scale(ratio, ratio)
        if hasattr(self, "_zoom_label"):
            self._zoom_label.setText(f"{int(self._zoom_factor * 100)}%")

    def _zoom_in(self) -> None:
        self._set_zoom_factor(self._zoom_factor * 1.15)

    def _zoom_out(self) -> None:
        self._set_zoom_factor(self._zoom_factor / 1.15)

    def _reset_zoom(self) -> None:
        self._set_zoom_factor(1.0)

    def _fit_zoom(self) -> None:
        self._zoom_factor = 1.0
        self._apply_graph_zoom(fit=True)

    def _update_path_label(self) -> None:
        if not self._meta:
            return
        parts = []
        for node_id in self._meta.active_path:
            node = self._meta.chapter_nodes.get(node_id)
            if node:
                if node.get("virtual"):
                    parts.append("第零章")
                else:
                    parts.append(f"第{node.get('chapter_num')}章 v{node.get('version')}")
        self._path_label.setText("活跃路径: " + (" → ".join(parts) if parts else "未设置"))

    def _selected_node(self) -> dict | None:
        if not self._selected_node_id or not self._meta:
            return None
        return self._meta.chapter_nodes.get(self._selected_node_id)

    def _select_node(self, node_id: str) -> None:
        self._selected_node_id = node_id
        active = set(self._meta.active_path if self._meta else [])
        for item_id, item in self._node_items.items():
            item._apply_style(active=item_id in active, selected=item_id == node_id)
        self._on_tree_selection()

    def _on_tree_selection(self, current=None, previous=None) -> None:
        self._current_node = self._selected_node()
        if not self._current_node:
            self._details.setPlainText("请选择章节节点。")
            return
        node = self._current_node
        is_virtual = bool(node.get("virtual"))
        for button in (
            self._edit_btn, self._polish_btn, self._rewrite_btn,
            self._export_btn, self._delete_btn, self._summary_btn,
        ):
            button.setEnabled(not is_virtual)
        if is_virtual:
            self._details.setPlainText(
                "章节: 第零章\n"
                "标题: 故事起点\n"
                "类型: 虚拟根节点\n\n"
                "选择该节点并点击“切换分支”，可将活跃路径重置为无正文章节状态。\n"
                "剧情摘要会恢复为“故事刚刚开始”，世界书会重建为空世界书。"
            )
            return
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
            "节点剧情摘要:",
            self._novel_manager.get_chapter_node_summary(
                self._book_title, int(node["chapter_num"]), int(node["version"])
            ) or "(未生成)",
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
            self._switch_btn.setEnabled(False)
            self._rebuild_success_message = "剧情记忆和世界书已按活跃路径同步。"
            self._details.setPlainText("正在按活跃路径同步剧情记忆和世界书，请稍候...")
            threading.Thread(target=self._run_rebuild_memory, daemon=True).start()

    def _run_rebuild_memory(self) -> None:
        try:
            self._novel_manager.rebuild_plot_summary_from_tree(self._book_title)
            report = self._novel_manager.rebuild_world_bible_from_active(
                self._api_client("chapter_tree_world_bible") if self._client else None,
                self._book_title,
                model=self._client.model if self._client else "deepseek-v4-flash",
                global_user_prompt=self._client.global_user_prompt if self._client else "",
            )
            self.rebuild_done.emit(report)
        except Exception as exc:
            self.rebuild_failed.emit(str(exc))

    def _on_rebuild_done(self, report=None) -> None:
        self._switch_btn.setEnabled(True)
        self._load_tree()
        report = report or {}
        details = (
            f"\n活跃章节：{report.get('active_chapters', 0)}"
            f"\n使用快照：{report.get('snapshot_count', 0)}"
            f"\n快照缺失：{report.get('snapshot_missing_count', 0)}"
            f"\n缺失快照已跳过：{report.get('snapshot_skipped_count', 0)}"
            f"\n正文补提取：{report.get('extracted_count', 0)}"
            f"\n缺失章节：{len(report.get('missing_chapters', []))}"
            f"\n提取失败：{len(report.get('failed_chapters', []))}"
        )
        QMessageBox.information(self, "完成", self._rebuild_success_message + details)

    def _on_rebuild_failed(self, error: str) -> None:
        self._switch_btn.setEnabled(True)
        QMessageBox.warning(self, "同步失败", error)
        self._load_tree()

    def _force_extract_all_world_bible(self) -> None:
        if not self._client:
            QMessageBox.warning(self, "无法提取", "当前没有可用的模型客户端。")
            return
        reply = QMessageBox.question(
            self,
            "提取全部活跃路径世界书",
            "将读取当前活跃路径上的全部章节正文，重新调用模型提取世界书。\n"
            "这个操作用于修复或刷新世界书，耗时和消耗会高于普通同步。\n\n继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._rebuild_success_message = "世界书已从全部活跃路径正文重新提取。"
        self._details.setPlainText("正在从活跃章节正文重新提取世界书，请稍候...")
        threading.Thread(target=self._run_force_extract_all_world_bible, daemon=True).start()

    def _run_force_extract_all_world_bible(self) -> None:
        try:
            report = self._novel_manager.rebuild_world_bible_from_active(
                self._api_client("chapter_tree_world_bible"),
                self._book_title,
                model=self._client.model,
                global_user_prompt=self._client.global_user_prompt,
                force_extract=True,
            )
            self.rebuild_done.emit(report)
        except Exception as exc:
            self.rebuild_failed.emit(str(exc))

    def _force_extract_current_world_bible(self) -> None:
        node = self._selected_node()
        if not node or node.get("virtual"):
            QMessageBox.warning(self, "未选择章节", "请先选择一个正文章节节点。")
            return
        if not self._client:
            QMessageBox.warning(self, "无法提取", "当前没有可用的模型客户端。")
            return
        reply = QMessageBox.question(
            self,
            "提取当前章节世界书",
            f"只重新提取第{node.get('chapter_num')}章 v{node.get('version')} 的世界书快照，"
            "然后按活跃路径重新合并世界书。\n\n继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._rebuild_success_message = (
            f"第{node.get('chapter_num')}章 v{node.get('version')} 世界书快照已刷新。"
        )
        self._details.setPlainText("正在提取当前章节世界书，请稍候...")
        threading.Thread(
            target=self._run_force_extract_current_world_bible,
            args=(node["id"],),
            daemon=True,
        ).start()

    def _run_force_extract_current_world_bible(self, node_id: str) -> None:
        try:
            report = self._novel_manager.extract_world_bible_for_node(
                self._api_client("chapter_tree_world_bible"),
                self._book_title,
                node_id,
                model=self._client.model,
                global_user_prompt=self._client.global_user_prompt,
            )
            self.rebuild_done.emit(report)
        except Exception as exc:
            self.rebuild_failed.emit(str(exc))

    def _regenerate_summary(self) -> None:
        node = self._selected_node()
        if not node or node.get("virtual"):
            QMessageBox.warning(self, "未选择章节", "请先选择一个正文章节节点。")
            return
        if not self._client:
            QMessageBox.warning(self, "无法生成", "当前没有可用的模型客户端。")
            return
        self._summary_btn.setEnabled(False)
        self._details.setPlainText("正在重新生成当前章节摘要，请稍候...")
        threading.Thread(
            target=self._run_regenerate_summary,
            args=(dict(node),),
            daemon=True,
        ).start()

    def _run_regenerate_summary(self, node: dict) -> None:
        old_summary = ""
        try:
            content = self._novel_manager.read_chapter_node(self._book_title, node["id"]) or ""
            if not content.strip():
                raise ValueError("当前章节正文为空。")
            chapter_num = int(node.get("chapter_num", 0) or 0)
            version = int(node.get("version", 0) or 0)
            title = node.get("title") or f"第{chapter_num}章"
            old_summary = self._novel_manager.get_chapter_node_summary(
                self._book_title, chapter_num, version
            )
            summary = self._novel_manager.generate_summary(
                self._api_client("chapter_tree_summary"),
                content,
                chapter_num,
                title,
                model=self._client.model,
                global_user_prompt=self._client.global_user_prompt,
                xp_mode=self._novel_manager.load_meta(self._book_title).xp_mode,
                raise_on_error=True,
            )
            if not summary.strip():
                raise RuntimeError("模型返回了空摘要。节点摘要未修改，也未保存任何新内容。")
            self._novel_manager.set_chapter_node_summary(
                self._book_title, chapter_num, version, summary
            )
            self._novel_manager.rebuild_plot_summary_from_tree(self._book_title)
            self.summary_done.emit(f"第{chapter_num}章 v{version} 摘要已重新生成。")
        except Exception as exc:
            old_state = "原有摘要已保留" if old_summary else "节点摘要仍为空"
            self.summary_failed.emit(
                f"{exc}\n\n{old_state}；本次未修改、未保存摘要。"
            )

    def _on_summary_done(self, message: str) -> None:
        self._summary_btn.setEnabled(True)
        self._load_tree()
        QMessageBox.information(self, "完成", message)

    def _on_summary_failed(self, message: str) -> None:
        self._summary_btn.setEnabled(True)
        self._load_tree()
        QMessageBox.warning(self, "摘要生成失败", message)

    def _edit_current(self) -> None:
        node = self._selected_node()
        if not node or node.get("virtual"):
            return
        dialog = ChapterEditorDialog(self, self._novel_manager, self._book_title, node)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._load_tree()

    def _safe_export_name(self, text: str) -> str:
        return re.sub(r'[\\/:*?"<>|]', "_", text).strip() or "未命名"

    def _export_current(self) -> None:
        node = self._selected_node()
        if not node or node.get("virtual"):
            QMessageBox.warning(self, "未选择章节", "请先在左侧章节树中选择一个章节节点。")
            return

        content = self._novel_manager.read_chapter_node(self._book_title, node["id"]) or ""
        if not content.strip():
            QMessageBox.warning(self, "内容为空", "当前章节没有可导出的正文。")
            return

        chapter_num = int(node.get("chapter_num", 0) or 0)
        version = int(node.get("version", 0) or 0)
        chapter_title = node.get("title", "") or f"第{chapter_num}章"
        safe_book = self._safe_export_name(self._book_title)
        safe_title = self._safe_export_name(chapter_title)
        default_name = f"{safe_book}_第{chapter_num}章_v{version}_{safe_title}.txt"
        output_path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "导出该章节",
            default_name,
            "纯文本 (*.txt);;Markdown (*.md)",
        )
        if not output_path:
            return

        selected_filter = selected_filter or ""
        if "." not in output_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]:
            output_path += ".md" if "Markdown" in selected_filter else ".txt"

        header = f"第{chapter_num}章 {chapter_title}（v{version}）"
        if output_path.lower().endswith(".md"):
            text = f"# {header}\n\n{content.strip()}\n"
        else:
            text = f"{header}\n\n{content.strip()}\n"

        try:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(text)
            QMessageBox.information(self, "导出成功", f"章节已导出到：\n{output_path}")
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", f"导出出错：{exc}")

    def _generate_variant(self, mode: str) -> None:
        node = self._selected_node()
        if not node or node.get("virtual") or not self._client:
            return
        label = "润色要求" if mode == "polish" else "重写要求"
        requirement, ok = QInputDialog.getMultiLineText(self, label, f"请输入{label}：")
        if not ok or not requirement.strip():
            return
        if mode == "polish" and self._novel_generation_mode == "agent":
            self._prepare_agent_polish(dict(node), requirement.strip())
            return
        self._start_variant_generation(dict(node), mode, requirement.strip())

    def _prepare_agent_polish(self, node: dict, requirement: str) -> None:
        from core.agent.chapter_polish import AgentChapterPolishService, AgentPolishRequest

        chapter_num = int(node.get("chapter_num", 0) or 0)
        title = node.get("title") or f"第{chapter_num}章"
        request = AgentPolishRequest(
            book_title=self._book_title,
            node_id=node["id"],
            chapter_num=chapter_num,
            chapter_title=title,
            requirement=requirement,
            model=self._client.model,
            global_prompt=self._client.global_user_prompt,
        )
        self._polish_btn.setEnabled(False)
        self._rewrite_btn.setEnabled(False)
        self._details.setPlainText("Agent 正在分析原文、润色要求和连续性上下文，请稍候...")
        parent = self.parent()
        if hasattr(parent, "_agent_chapter_planning"):
            parent._agent_chapter_planning = True

        def prepare() -> None:
            try:
                service = AgentChapterPolishService(
                    self._novel_manager, self._api_client("agent_chapter_polish_plan"),
                    skills_enabled=self._skills_enabled,
                )
                plan = service.prepare(request)
                self.polish_plan_ready.emit(node, request, plan)
            except Exception as exc:
                self.polish_plan_failed.emit(str(exc))

        threading.Thread(target=prepare, daemon=True).start()

    def _on_polish_plan_ready(self, node, request, plan) -> None:
        parent = self.parent()
        if hasattr(parent, "_agent_chapter_planning"):
            parent._agent_chapter_planning = False
        self._polish_btn.setEnabled(True)
        self._rewrite_btn.setEnabled(True)
        self._details.setPlainText(plan.render())
        if plan.rewrite_required:
            reasons = "\n".join(f"- {item}" for item in plan.rewrite_reasons)
            QMessageBox.warning(
                self,
                "请使用重写",
                "该要求涉及剧情、事实或人物行为修改，不能作为润色执行。\n\n"
                + (reasons or "请改用章节树中的“重写”功能。"),
            )
            from core.agent.chapter_polish import AgentChapterPolishService
            AgentChapterPolishService(
                self._novel_manager, self._api_client("agent_chapter_polish_plan"),
                skills_enabled=self._skills_enabled,
            ).mark_cancelled(request, plan)
            return
        from ui.agent_polish_dialog import AgentPolishPlanDialog
        dialog = AgentPolishPlanDialog(self, request, plan)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            from core.agent.chapter_polish import AgentChapterPolishService
            AgentChapterPolishService(
                self._novel_manager, self._api_client("agent_chapter_polish_plan"),
                skills_enabled=self._skills_enabled,
            ).mark_cancelled(request, plan)
            self._details.setPlainText("Agent 润色方案已取消，未修改章节树。")
            return
        self._start_variant_generation(
            dict(node), "polish", request.requirement,
            agent_request=request, agent_plan=plan,
        )

    def _on_polish_plan_failed(self, error: str) -> None:
        parent = self.parent()
        if hasattr(parent, "_agent_chapter_planning"):
            parent._agent_chapter_planning = False
        self._polish_btn.setEnabled(True)
        self._rewrite_btn.setEnabled(True)
        self._details.setPlainText(f"Agent 润色规划失败：{error}")
        QMessageBox.critical(self, "Agent 润色规划失败", error)

    def _start_variant_generation(
        self, node: dict, mode: str, requirement: str, *,
        agent_request=None, agent_plan=None,
    ) -> None:
        self._details.setPlainText("正在生成新版本，请稍候...")
        self._polish_btn.setEnabled(False)
        self._rewrite_btn.setEnabled(False)
        parent = self.parent()
        if hasattr(parent, "_stream_chapter_completion") and hasattr(parent, "_client"):
            chapter_num = int(node.get("chapter_num", 0) or 0)
            title = node.get("title") or f"第{chapter_num}章"
            parent._chapter_finalized = False
            parent._generate_btn.setEnabled(False)
            if hasattr(parent, "_agent_generate_btn"):
                parent._agent_generate_btn.setEnabled(False)
            parent._cont_generate_btn.setEnabled(False)
            parent._client.reset_cancel()
            parent._stop_btn.setVisible(True)
            parent._stop_btn.setEnabled(True)
            parent._stop_btn.setText("⏹")
            parent._mode_combo.setEnabled(False)
            parent._streaming = True
            parent._streaming_start_time = time.time()
            parent._assistant_text_buffer = []
            action = "Agent 润色" if agent_plan is not None else ("润色" if mode == "polish" else "重写")
            parent._append_user_message(f"🌳 {action}第 {chapter_num} 章「{title}」")
        threading.Thread(
            target=self._run_generation,
            args=(node, mode, requirement),
            kwargs={"agent_request": agent_request, "agent_plan": agent_plan},
            daemon=True,
        ).start()

    def _run_generation(
        self, node: dict, mode: str, requirement: str, *,
        agent_request=None, agent_plan=None,
    ) -> None:
        host_stream_finished = False
        try:
            old_content = self._novel_manager.read_chapter_node(self._book_title, node["id"]) or ""
            chapter_num = int(node["chapter_num"])
            title = node.get("title") or f"第{chapter_num}章"
            messages = [{"role": "system", "content": Prompts.NOVEL_CHAPTER_WRITING}]
            polish_service = None
            if agent_plan is not None and agent_request is not None:
                from core.agent.chapter_polish import AgentChapterPolishService
                polish_service = AgentChapterPolishService(
                    self._novel_manager, self._api_client("agent_chapter_polish_review"),
                    skills_enabled=self._skills_enabled,
                )
                user_prompt, old_content = polish_service.build_prompt(agent_request, agent_plan)
                operation = "agent_chapter_polish"
            elif mode == "polish":
                user_prompt = (
                    "请基于以下章节全文进行润色，保留核心剧情，不要输出解释。\n\n"
                    f"【润色要求】\n{requirement}\n\n【原章节】\n{old_content}"
                )
                operation = "chapter_tree_polish"
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
                operation = "chapter_tree_rewrite"
            messages.append({"role": "user", "content": user_prompt})
            parent = self.parent()
            if hasattr(parent, "_stream_chapter_completion"):
                content, _generation_stats, cancelled = parent._stream_chapter_completion(
                    operation=operation,
                    messages=messages,
                    prompt_text=user_prompt,
                    max_tokens=self._client.max_tokens,
                )
                if cancelled:
                    parent._stream_signals.token.emit("\n\n⏹️ 已取消\n")
                    if polish_service is not None:
                        polish_service.mark_cancelled(agent_request, agent_plan)
                    self._finish_host_stream()
                    host_stream_finished = True
                    self.generation_failed.emit("已取消生成。")
                    return
            else:
                response = self._api_client(operation).chat.completions.create(
                    model=self._client.model,
                    messages=messages,
                    temperature=self._client.temperature,
                    top_p=self._client.top_p,
                    max_tokens=self._client.max_tokens,
                    frequency_penalty=self._client.frequency_penalty,
                    stream=False,
                )
                content = response.choices[0].message.content or ""

            fidelity_report = None
            if polish_service is not None:
                if hasattr(parent, "_stream_signals"):
                    parent._stream_signals.token.emit("\n🔍 正在执行润色保真审查...\n")
                validation = polish_service.validate_and_repair(
                    agent_request, agent_plan, old_content, content
                )
                fidelity_report = validation.report
                content = validation.content
                if not validation.passed:
                    self._finish_host_stream()
                    host_stream_finished = True
                    self.generation_failed.emit(
                        "润色稿未通过保真审查，未写入章节树。"
                        f"\n加密 Artifact：{validation.artifact_id}"
                    )
                    return

            if getattr(self._client, "_cancel_requested", False):
                if polish_service is not None:
                    polish_service.mark_cancelled(agent_request, agent_plan)
                self._finish_host_stream()
                host_stream_finished = True
                self.generation_failed.emit("已取消生成。")
                return

            version = self._novel_manager.get_next_version(self._book_title, chapter_num)
            _, version = self._novel_manager.save_chapter_version(
                self._book_title, chapter_num, title, content,
                version=version, parent_id=node.get("parent_id"),
            )
            summary = self._novel_manager.generate_summary(
                self._api_client("chapter_tree_summary"), content, chapter_num, title,
                model=self._client.model,
                global_user_prompt=self._client.global_user_prompt,
                xp_mode=self._novel_manager.load_meta(self._book_title).xp_mode,
            )
            if summary.strip():
                self._novel_manager.set_chapter_node_summary(self._book_title, chapter_num, version, summary)
            self._novel_manager.rebuild_plot_summary_from_tree(self._book_title)
            self._novel_manager.save_generation_record(
                self._book_title, chapter_num, title, version, user_prompt,
                self._client.model, self._client.temperature, self._client.top_p,
                self._client.max_tokens, self._client.frequency_penalty, content[:500],
                requirement=requirement,
                generation_mode="agent" if agent_plan is not None else "classic",
                agent_run_id=agent_plan.plan_id if agent_plan is not None else None,
                operation="chapter_polish" if mode == "polish" else "chapter_rewrite",
                polish_requirement=requirement if mode == "polish" else "",
                polish_plan=agent_plan.to_dict() if agent_plan is not None else None,
                fidelity_report=fidelity_report,
            )
            world_bible_warning = ""
            try:
                self._novel_manager.extract_world_bible_for_node(
                    self._api_client("chapter_tree_world_bible"), self._book_title,
                    self._novel_manager._node_id(chapter_num, version),
                    model=self._client.model,
                    global_user_prompt=self._client.global_user_prompt,
                    xp_mode=self._novel_manager.load_meta(self._book_title).xp_mode,
                )
            except Exception as exc:
                world_bible_warning = f"\n世界书快照提取失败：{exc}"
            snapshot_id = ""
            try:
                snapshot = self._novel_manager.snapshot_service(self._book_title).create(
                    f"第{chapter_num}章 v{version} 润色完成" if mode == "polish" else f"第{chapter_num}章 v{version} 重写完成",
                    source="chapter",
                )
                snapshot_id = snapshot.snapshot_id
            except Exception as exc:
                world_bible_warning += f"\n项目快照失败：{exc}"
            if polish_service is not None:
                polish_service.mark_completed(agent_request, agent_plan, version, snapshot_id)
            self._finish_host_stream()
            host_stream_finished = True
            self.generation_done.emit(
                f"已生成新版本 v{version}，未自动切换活跃版本。{world_bible_warning}"
            )
        except Exception as exc:
            if not host_stream_finished:
                self._finish_host_stream()
            self.generation_failed.emit(str(exc))
    def _on_generation_done(self, message: str) -> None:
        self._polish_btn.setEnabled(True)
        self._rewrite_btn.setEnabled(True)
        QMessageBox.information(self, "完成", message)
        self._load_tree()

    def _on_generation_failed(self, message: str) -> None:
        self._polish_btn.setEnabled(True)
        self._rewrite_btn.setEnabled(True)
        if "已取消" in message:
            QMessageBox.information(self, "已取消", message)
        else:
            QMessageBox.critical(self, "生成失败", message)
        self._load_tree()

    def _insert_agent_extra(self) -> None:
        node = self._selected_node()
        if not node or node.get("virtual") or not self._client:
            QMessageBox.warning(self, "Agent 番外", "请先选择一个正文章节节点。")
            return
        if self._novel_generation_mode != "agent":
            QMessageBox.information(self, "Agent 番外", "请先在设置中心切换到 Agent 写作模式。")
            return
        from ui.agent_extra_dialog import AgentExtraRequestDialog
        dialog = AgentExtraRequestDialog(self, start_node=node, reference_node=node)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        extra_type = values["extra_type"]
        start_node_id = node["id"] if extra_type in {"enrichment", "if_line"} else ""
        reference_node_id = node["id"] if extra_type in {"prequel", "sequel"} else ""
        end_node_id = ""
        if extra_type in {"enrichment", "if_line"}:
            children = [self._meta.chapter_nodes[item] for item in node.get("children_ids", []) if item in self._meta.chapter_nodes]
            if not children:
                QMessageBox.warning(self, "Agent 番外", "当前节点没有直接下一节点，无法选择连续的两个点。")
                return
            labels = [f"{item.get('display_label') or item.get('title')} [{item['id']}]" for item in children]
            selected, ok = QInputDialog.getItem(self, "选择终点", "请选择与起点直接相连的下一节点：", labels, 0, False)
            if not ok:
                return
            end_node_id = children[labels.index(selected)]["id"]
        from core.agent.extra_generation import AgentExtraGenerationService, AgentExtraRequest
        request = AgentExtraRequest(
            book_title=self._book_title,
            extra_type=extra_type,
            start_node_id=start_node_id,
            end_node_id=end_node_id,
            reference_node_id=reference_node_id,
            title=values["title"],
            plot=values["plot"],
            requirement=values["requirement"],
            target_words=values["target_words"],
            model=self._client.model,
            manual_entity_ids=values["manual_entity_ids"],
            global_prompt=self._client.global_user_prompt,
        )
        self._extra_btn.setEnabled(False)
        self._details.setPlainText("Agent 正在分析番外位置、上下文、世界书和历史剧情……")
        parent = self.parent()
        if hasattr(parent, "_agent_chapter_planning"):
            parent._agent_chapter_planning = True

        def prepare() -> None:
            try:
                service = AgentExtraGenerationService(
                    self._novel_manager,
                    self._api_client("agent_extra_plan"),
                    skills_enabled=self._skills_enabled,
                )
                plan = service.prepare(request)
                self.extra_plan_ready.emit(request, plan)
            except Exception as exc:
                self.extra_plan_failed.emit(str(exc))
        threading.Thread(target=prepare, daemon=True).start()

    def _on_extra_plan_ready(self, request, plan) -> None:
        parent = self.parent()
        if hasattr(parent, "_agent_chapter_planning"):
            parent._agent_chapter_planning = False
        self._extra_btn.setEnabled(True)
        self._details.setPlainText(plan.render())
        from ui.agent_extra_dialog import AgentExtraPlanDialog
        if AgentExtraPlanDialog(self, request, plan).exec() != QDialog.DialogCode.Accepted:
            self._details.setPlainText("番外计划已取消，未修改章节森林。")
            return
        self._start_extra_generation(request, plan)

    def _on_extra_plan_failed(self, error: str) -> None:
        parent = self.parent()
        if hasattr(parent, "_agent_chapter_planning"):
            parent._agent_chapter_planning = False
        self._extra_btn.setEnabled(True)
        self._details.setPlainText(f"Agent 番外规划失败：{error}")
        QMessageBox.critical(self, "Agent 番外规划失败", error)

    def _start_extra_generation(self, request, plan) -> None:
        self._extra_btn.setEnabled(False)
        self._details.setPlainText("正在生成番外正文，请稍候……")
        parent = self.parent()
        if hasattr(parent, "_stream_chapter_completion"):
            parent._chapter_finalized = False
            parent._generate_btn.setEnabled(False)
            if hasattr(parent, "_agent_generate_btn"):
                parent._agent_generate_btn.setEnabled(False)
            parent._cont_generate_btn.setEnabled(False)
            parent._client.reset_cancel()
            parent._stop_btn.setVisible(True)
            parent._stop_btn.setEnabled(True)
            parent._mode_combo.setEnabled(False)
            parent._streaming = True
            parent._streaming_start_time = time.time()
            parent._assistant_text_buffer = []
            parent._append_user_message(f"Agent 生成番外：{request.title}")
        threading.Thread(target=self._run_extra_generation, args=(request, plan), daemon=True).start()

    def _run_extra_generation(self, request, plan) -> None:
        host_finished = False
        rollback_snapshot_id = ""
        try:
            from core.agent.extra_generation import AgentExtraGenerationService
            service = AgentExtraGenerationService(
                self._novel_manager,
                self._api_client("agent_extra_prompt"),
                skills_enabled=self._skills_enabled,
            )
            result = service.generate(request, plan)
            messages = [
                {"role": "system", "content": Prompts.NOVEL_CHAPTER_WRITING},
                {"role": "user", "content": result.prompt},
            ]
            parent = self.parent()
            if hasattr(parent, "_stream_chapter_completion"):
                content, generation_stats, cancelled = parent._stream_chapter_completion(
                    operation="agent_extra_generation",
                    messages=messages,
                    prompt_text=result.prompt,
                    max_tokens=max(request.target_words * 2, self._client.max_tokens),
                )
                if cancelled:
                    self._finish_host_stream()
                    host_finished = True
                    self.generation_failed.emit("已取消番外生成。")
                    return
            else:
                response = self._api_client("agent_extra_generation").chat.completions.create(
                    model=self._client.model, messages=messages,
                    temperature=self._client.temperature, top_p=self._client.top_p,
                    max_tokens=max(request.target_words * 2, self._client.max_tokens),
                    frequency_penalty=self._client.frequency_penalty,
                )
                content = response.choices[0].message.content or ""
                generation_stats = {}
            if getattr(self._client, "_cancel_requested", False):
                self._finish_host_stream()
                host_finished = True
                self.generation_failed.emit("已取消番外生成。")
                return
            anchor_id = request.start_node_id or request.reference_node_id
            anchor = self._novel_manager.ensure_chapter_tree(self._book_title).chapter_nodes.get(anchor_id, {})
            chapter_num = int(anchor.get("chapter_num", 0) or 0)
            if hasattr(parent, "_supervise_chapter_content"):
                content, supervision_report = parent._supervise_chapter_content(
                    chapter_num=chapter_num,
                    chapter_title=request.title,
                    content=content,
                    context=result.prompt,
                    chapter_outline=request.plot,
                    requirements=request.requirement,
                    target_words=request.target_words,
                    xp_mode=self._novel_manager.load_meta(self._book_title).xp_mode,
                    operation_prefix="agent_extra",
                    agent_mode=True,
                )
            else:
                supervision_report = {"status": "not_available"}
            summary = self._novel_manager.generate_summary(
                self._api_client("agent_extra_summary"), content, chapter_num, request.title,
                model=self._client.model,
                global_user_prompt=self._client.global_user_prompt,
                xp_mode=self._novel_manager.load_meta(self._book_title).xp_mode,
                raise_on_error=True,
            )
            rollback = self._novel_manager.snapshot_service(self._book_title).create(
                f"插入番外「{request.title}」前备份", source="rollback_backup"
            )
            rollback_snapshot_id = rollback.snapshot_id
            generation_record = {
                "schema_version": 1,
                "operation": "agent_extra_generation",
                "generation_mode": "agent",
                "agent_run_id": plan.plan_id,
                "extra_type": request.extra_type,
                "title": request.title,
                "prompt": result.prompt,
                "plot": request.plot,
                "requirement": request.requirement,
                "model": self._client.model,
                "temperature": self._client.temperature,
                "top_p": self._client.top_p,
                "max_tokens": self._client.max_tokens,
                "frequency_penalty": self._client.frequency_penalty,
                "generation_stats": generation_stats,
                "supervision_report": supervision_report,
                "plan": plan.to_dict(),
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            node = self._novel_manager.save_extra_node(
                self._book_title,
                run_id=plan.plan_id,
                extra_type=request.extra_type,
                chapter_title=request.title,
                content=content,
                start_node_id=request.start_node_id,
                end_node_id=request.end_node_id,
                reference_node_id=request.reference_node_id,
                summary=summary,
                generation_record=generation_record,
            )
            self._novel_manager.extract_world_bible_for_extra_node(
                self._api_client("agent_extra_world_bible"),
                self._book_title, node["id"],
                model=self._client.model,
                global_user_prompt=self._client.global_user_prompt,
                xp_mode=self._novel_manager.load_meta(self._book_title).xp_mode,
                rebuild_active=request.extra_type == "enrichment",
            )
            if request.extra_type == "enrichment":
                self._novel_manager.rebuild_plot_summary_from_tree(self._book_title)
            completed = self._novel_manager.snapshot_service(self._book_title).create(
                f"番外「{request.title}」生成完成", source="chapter"
            )
            service.mark_completed(self._book_title, plan.plan_id, node["id"], completed.snapshot_id)
            self._finish_host_stream()
            host_finished = True
            self.generation_done.emit(
                f"番外已生成：{node.get('display_label')}「{request.title}」。"
                + ("已插入当前活跃路径。" if request.extra_type == "enrichment" else "当前正传活跃路径未改变。")
            )
        except Exception as exc:
            if rollback_snapshot_id:
                try:
                    self._novel_manager.snapshot_service(self._book_title).restore(rollback_snapshot_id)
                except Exception as restore_exc:
                    exc = RuntimeError(f"{exc}；回滚失败：{restore_exc}")
            if not host_finished:
                self._finish_host_stream()
            self.generation_failed.emit(str(exc))

    def _delete_current(self) -> None:
        node = self._selected_node()
        if not node or node.get("virtual"):
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
        self._switch_btn.setEnabled(False)
        self._rebuild_success_message = "章节子树已删除，剧情记忆和世界书已按剩余活跃路径同步。"
        self._details.setPlainText("正在同步删除后的剧情记忆和世界书，请稍候...")
        threading.Thread(target=self._run_rebuild_memory, daemon=True).start()



