"""
DeepSeek 多功能聊天客户端 - GUI 入口
基于 PyQt6 图形界面 + QWebEngineView Markdown 实时渲染
"""
import sys

from ui.main_window import run_gui


if __name__ == "__main__":
    run_gui()