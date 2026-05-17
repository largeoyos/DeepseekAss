"""
字数统计与补充模块
提供中文字数统计和内容补充（当生成字数不足时调用 API 续写）功能
"""

import re


def count_cn(text: str) -> int:
    """统计文本中的中文字符数"""
    return len(re.findall(r'[一-鿿]', text))


def supplement_content(
    client,
    original_content: str,
    target_chars: int,
    actual_chars: int,
    chapter_title: str,
    model: str,
    temperature: float = 0.7,
    global_user_prompt: str = "",
) -> str:
    """
    当生成内容字数不足时，调用 API 补充内容

    继承自 WriteTool expand_novel.py 的 supplement_section 算法。

    Args:
        client: OpenAI 兼容客户端
        original_content: 已生成的正文
        target_chars: 目标中文字数
        actual_chars: 实际中文字数
        chapter_title: 章节标题（用于提示）
        model: 模型名称
        temperature: 生成温度
        global_user_prompt: 用户全局提示词（写作偏好）

    Returns:
        补充内容文本（不包含已有内容）
    """
    prompt_parts = [
        f"你正在续写小说章节「{chapter_title}（续）」。\n"
        f"当前正文已有 {actual_chars} 字，目标字数 {target_chars} 字。\n"
        f"请直接输出额外的正文内容，与现有内容自然衔接，保持风格一致。\n"
        f"不要重复已有内容，不要添加任何解释或前言。\n\n"
        f"{original_content[-1500:]}\n\n（以上为当前章节末尾，请从此处继续写）"
    ]
    if global_user_prompt.strip():
        prompt_parts.append(f"\n【用户偏好提示】: \n{global_user_prompt}")
    prompt = "\n".join(prompt_parts)
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=min(target_chars * 2, 32768),
            temperature=temperature,
        )
        return response.choices[0].message.content or ""
    except Exception:
        return ""
