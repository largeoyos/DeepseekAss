"""
DeepSeek 多功能聊天客户端 - 入口程序
基于策略模式的 CLI 交互式聊天工具
"""

import sys

from config import Config
from core.chat_client import DeepSeekChatClient
from strategies import (
    RolePlayStrategy,
    NovelStrategy,
    CodeAssistantStrategy,
)

# ---------- 模式注册表 ----------
# 如需新增模式，只需在此添加键值对即可：
#   1. 在 strategies/ 下新建策略类（继承 BaseStrategy）
#   2. 在 utils/prompts.py 添加对应的 System Prompt
#   3. 在此注册表中添加一项
STRATEGY_REGISTRY: dict[str, type] = {
    "1": RolePlayStrategy,
    "2": NovelStrategy,
    "3": CodeAssistantStrategy,
}

MODE_DESCRIPTIONS = {
    "1": "角色扮演 - 模拟特定人物/身份的对话风格",
    "2": "小说写作 - 创意写作、情节构思、文笔润色",
    "3": "代码助手 - 编程帮助、Debug、代码审查",
}

MODEL_CHOICES = {
    "1": Config.MODEL_V4_FLASH,
    "2": Config.MODEL_V4_PRO,
}


# ========== UI 辅助函数 ==========

def print_banner() -> None:
    """打印欢迎横幅"""
    print()
    print("=" * 52)
    print("   🚀  DeepSeek 多功能聊天客户端")
    print("=" * 52)


def print_mode_menu() -> None:
    """打印模式选择菜单"""
    print("\n请选择聊天模式：")
    for key, desc in MODE_DESCRIPTIONS.items():
        print(f"  [{key}] {desc}")
    print("  [0] 退出程序")
    print("-" * 40)


def print_model_menu() -> None:
    """打印模型选择菜单"""
    print("\n请选择模型：")
    print("  [1] deepseek-v4-flash  (v4 闪电版)")
    print("  [2] deepseek-v4-pro    (v4 专业版)")
    print("  [默认] 直接回车使用推荐模型")
    print("-" * 40)


def print_help() -> None:
    """打印聊天内帮助"""
    print()
    print("⌨️  可用命令：")
    print("  /help     - 显示此帮助")
    print("  /model    - 切换模型")
    print("  /temp     - 查看/设置温度参数")
    print("  /clear    - 清除对话上下文")
    print("  /history  - 显示当前对话历史摘要")
    print("  /mode     - 返回模式选择")
    print("  /quit     - 退出程序")
    print()


def print_model_info(client: DeepSeekChatClient) -> None:
    """打印当前模型信息"""
    print(f"\n📋 当前状态：")
    print(f"   模式: {client.strategy.get_name()}")
    print(f"   模型: {client.model}")
    print(f"   温度: {client.temperature}")


# ========== 主交互循环 ==========

def interactive_chat(client: DeepSeekChatClient) -> bool:
    """
    进入对话交互循环

    Returns:
        True: 用户请求返回模式选择菜单
        False: 用户请求退出
    """
    strategy = client.strategy
    print(f"\n{strategy.get_welcome_message()}")
    print_model_info(client)
    print_help()

    while True:
        try:
            user_input = input("\n🧑 你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\n再见！")
            return False

        if not user_input:
            continue

        # ----- 命令处理 -----
        if user_input.startswith("/"):
            result = handle_command(user_input, client)
            if result == "quit":
                print("\n再见！")
                return False
            elif result == "mode_menu":
                return True  # 返回模式选择
            continue

        # ----- 正常对话 -----
        print("\n🤖 助手: ", end="", flush=True)
        try:
            # 使用流式输出
            for token in client.chat_stream(user_input):
                print(token, end="", flush=True)
            print()
        except RuntimeError as e:
            print(f"\n❌ 错误: {e}")


def handle_command(cmd: str, client: DeepSeekChatClient) -> str | None:
    """
    处理聊天内命令

    Returns:
        "quit": 退出程序
        "mode_menu": 返回模式选择
        None: 继续当前对话
    """
    cmd_lower = cmd.lower()

    if cmd_lower in ("/quit", "/exit", "/q"):
        return "quit"

    if cmd_lower == "/help":
        print_help()

    elif cmd_lower == "/model":
        _command_switch_model(client)

    elif cmd_lower.startswith("/temp"):
        _command_temperature(cmd, client)

    elif cmd_lower == "/clear":
        client.clear_context()
        print("✅ 对话上下文已清除（System Prompt 已保留）。")

    elif cmd_lower == "/history":
        _command_history(client)

    elif cmd_lower == "/mode":
        print("返回模式选择菜单...")
        return "mode_menu"

    else:
        print(f"未知命令: {cmd}。输入 /help 查看可用命令。")

    return None


def _command_switch_model(client: DeepSeekChatClient) -> None:
    """处理 /model 命令"""
    print_model_menu()
    choice = input("请选择模型 [1/2，默认跳过]: ").strip()
    model_map = {"1": Config.MODEL_V4_FLASH, "2": Config.MODEL_V4_PRO}
    if choice in model_map:
        client.switch_model(model_map[choice])
        print(f"✅ 已切换到模型: {client.model}")
    else:
        print("已取消，保持当前模型。")


def _command_temperature(cmd: str, client: DeepSeekChatClient) -> None:
    """处理 /temp 命令"""
    parts = cmd.split()
    if len(parts) > 1:
        try:
            new_temp = float(parts[1])
            client.set_temperature(new_temp)
            print(f"✅ 温度已设置为: {client.temperature}")
        except ValueError:
            print("❌ 请输入有效数值，如 /temp 0.5")
    else:
        print(f"当前温度: {client.temperature} (范围 0.0 ~ 2.0)")
        print("设置方法: /temp <数值>")


def _command_history(client: DeepSeekChatClient) -> None:
    """显示对话历史摘要"""
    msgs = client.messages
    if not msgs:
        print("对话历史为空。")
        return
    print(f"\n📜 对话历史 ({len(msgs)} 条消息):")
    for i, msg in enumerate(msgs):
        role = msg["role"]
        content_preview = msg["content"][:80].replace("\n", " ")
        print(f"  [{i}] {role}: {content_preview}...")


# ========== 启动流程 ==========

def main() -> None:
    """程序主入口"""
    # 验证配置
    try:
        Config.validate()
    except ValueError as e:
        print(f"❌ 配置错误: {e}")
        sys.exit(1)

    print_banner()

    while True:
        print_mode_menu()
        choice = input("请输入选项: ").strip()

        if choice == "0":
            print("再见！")
            break

        strategy_cls = STRATEGY_REGISTRY.get(choice)
        if strategy_cls is None:
            print("❌ 无效选项，请重新输入。")
            continue

        # 实例化策略
        strategy = strategy_cls()

        # 询问模型
        print_model_menu()
        model_choice = input("请选择模型 [1/2，默认使用推荐]: ").strip()
        model = MODEL_CHOICES.get(model_choice)

        # 创建客户端
        try:
            client = DeepSeekChatClient(strategy=strategy, model=model)
        except Exception as e:
            print(f"❌ 初始化失败: {e}")
            continue

        # 进入对话
        back_to_menu = interactive_chat(client)
        if not back_to_menu:
            break


if __name__ == "__main__":
    main()