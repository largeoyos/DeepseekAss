"""
字数统计与内容补充模块
提供中文字数统计和内容扩写（当生成字数不足时调用 API 扩写整章）功能
"""

import re

from utils.prompts import Prompts


def count_cn(text: str) -> int:
    """统计文本中的中文字符数"""
    return len(re.findall(r'[一-鿟]', text))


def supplement_content(
    client,
    original_content: str,
    target_chars: int,
    actual_chars: int,
    chapter_title: str,
    model: str,
    temperature: float = 0.7,
    global_user_prompt: str = "",
    protagonist_bio: str = "",
    background_story: str = "",
    writing_demand: str = "",
    world_bible_text: str = "",
    plot_content: str = "",
    history_summary: str = "",
) -> str:
    """
    字数不足时，将整章内容 + 全部上下文发送给 AI 进行扩写
    返回扩写后的完整章节内容（非追加片段），失败返回空字符串
    """
    parts = [
        "你是一位文笔细腻、想象力丰富的长篇小说作家。",
        f"下面是一章小说的当前版本（当前{actual_chars}字，目标{target_chars}字），",
        "字数不足需要扩写。",
        "",
        "【要求】",
        "1. 保留所有现有情节走向和已写内容，不可删减。",
        "2. 在现有基础上丰富细节描写——环境的光线/声音/气味氛围、角色的神态/动作/微表情、",
        "   对话中的语气停顿和肢体语言、内心活动和情绪转折，每个场景至少扩展2-3层细节。",
        "3. 保持人物性格、语言风格和世界观设定的一致性。",
        "4. 使章节更饱满、生动，达到目标字数。",
        "5. 直接输出扩写后的完整章节正文，不要添加任何解释或前言。",
        "",
    ]
    if protagonist_bio.strip():
        parts.append(f"【人物设定】\n{protagonist_bio}\n")
    if background_story.strip():
        parts.append(f"【世界观/背景】\n{background_story}\n")
    if writing_demand.strip():
        parts.append(f"【写作要求】\n{writing_demand}\n")
    if world_bible_text.strip():
        parts.append(f"【世界书（已建立设定库）】\n{world_bible_text}\n")
    if plot_content.strip():
        parts.append(f"【本章已定情节】\n{plot_content}\n")
    if history_summary.strip():
        parts.append(f"【历史生成参考】\n{history_summary}\n")
    if global_user_prompt.strip():
        parts.append(f"【用户偏好提示】\n{global_user_prompt}\n")

    parts.append(f"【当前章节正文】\n{original_content}\n")
    parts.append(
        "请基于以上设定扩写本章节正文，保留所有现有内容并丰富之。"
        "直接输出扩写后的完整章节。"
    )

    prompt = "\n".join(parts)

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=min(target_chars * 2, 32768),
            temperature=temperature,
        )
        result = response.choices[0].message.content or ""
        # 校验：结果不得比原内容短一半以上（防止 AI 胡编或返回空）
        cn_count = count_cn(result)
        if cn_count < actual_chars * 0.5:
            return ""
        return result
    except Exception:
        return ""
