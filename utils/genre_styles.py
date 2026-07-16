"""
题材与风格基调配置模块
双维度风格选择：题材（决定内容边界）+ 风格基调（决定文笔气质）
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class GenreConfig:
    """题材配置"""
    key: str
    display_name: str
    style_instruction: str
    temperature: float | None = None
    frequency_penalty: float | None = None


@dataclass
class ToneConfig:
    """风格基调配置"""
    key: str
    display_name: str
    style_instruction: str


# ========== 题材定义 ==========

GENRES: list[GenreConfig] = [
    GenreConfig("xianhuan", "玄幻/仙侠",
                "世界观宏大，修炼体系完整，力量等级分明。注重意境描写、功法对决、境界突破的刻画。",
                temperature=0.85, frequency_penalty=0.4),
    GenreConfig("qihuan", "奇幻",
                "西式奇幻或异世界背景，魔法、种族、神话体系自洽。注重场景氛围和冒险感。",
                temperature=0.85, frequency_penalty=0.4),
    GenreConfig("sci_fi", "科幻",
                "逻辑严谨，科技设定自洽，避免魔法式解释。注重未来感、技术细节和科学推理。",
                temperature=0.75, frequency_penalty=0.5),
    GenreConfig("history", "历史/架空",
                "尊重时代背景，语言符合历史语境，避免现代语病。注重历史厚重感和考据细节。",
                temperature=0.70, frequency_penalty=0.6),
    GenreConfig("urban", "都市/现代",
                "贴近现实生活，对话自然，社会逻辑合理。注重人物关系和日常氛围。",
                temperature=0.80, frequency_penalty=0.5),
    GenreConfig("suspense", "悬疑/惊悚",
                "节奏紧凑，伏笔回收严密，逻辑链完整。注重悬念营造和反转设计。",
                temperature=0.70, frequency_penalty=0.3),
    GenreConfig("wuxia", "武侠",
                "招式描写细腻，侠义精神贯穿，江湖规矩自洽。注重打斗场面和门派恩怨。",
                temperature=0.80, frequency_penalty=0.4),
    GenreConfig("romance", "言情",
                "情感细腻，心理描写丰富，CP互动自然。注重情绪张力和暧昧氛围。",
                temperature=0.90, frequency_penalty=0.3),
    GenreConfig("mo_app", "末世/生存",
                "生存压力贯穿始终，资源管理真实，人性抉择深刻。注重紧张感和环境描写。",
                temperature=0.80, frequency_penalty=0.4),
    GenreConfig("horror", "恐怖",
                "氛围营造优先，心理压迫感强，留白恰当。注重暗示而非直白描写。",
                temperature=0.75, frequency_penalty=0.2),
    GenreConfig("light_novel", "轻小说",
                "轻松诙谐，对话占比高，吐槽自然。注重角色反差萌和阅读节奏感。",
                temperature=0.90, frequency_penalty=0.3),
    GenreConfig("erotic", "色情",
                "情感铺垫到位，暧昧氛围渲染充分，感官描写细腻。注重情感渐进和氛围。",
                temperature=0.90, frequency_penalty=0.2),
    GenreConfig("none", "无特定风格",
                "",
                temperature=None, frequency_penalty=None),
]

# ========== 风格基调定义 ==========

STYLE_TONES: list[ToneConfig] = [
    ToneConfig("default", "默认", ""),
    ToneConfig("light", "轻快",
               "多用清楚利落的句子和自然口语推进场景；幽默来自人物处境、误会和反应，不连续抛俏皮话，也不把严肃后果轻轻带过。"),
    ToneConfig("serious", "严肃",
               "使用准确、克制的词语，减少感叹、夸饰和轻浮调侃；让判断建立在事实、行动及后果上，叙述者不替人物发表宏大议论。"),
    ToneConfig("literary", "文青/文艺",
               "选择少量贯穿场景的具体意象，利用句法节奏和留白形成余韵；比喻必须来自人物经验且点到即止，避免辞藻堆砌、万能景物和段尾升华。"),
    ToneConfig("restrained_prose", "朴素克制（散文感）",
               "采用白描和贴近日常说话的准确句子，从人物会留意的物件、动作和生活习惯落笔。少用形容词和比喻，不直接命名情绪，不解释主题；重要段落停在一个动作、一句对白或一件具体景物上，让余味由读者自己读出。"),
    ToneConfig("dark", "暗黑",
               "用可验证的处境、代价和人物选择积累压迫感；残酷场面写清因果和身体后果，少堆阴冷形容词、黑暗意象和故作深沉的心理结论。"),
    ToneConfig("passionate", "热血",
               "以清楚的行动目标、阻力、配合和代价推动快节奏场面；情绪高潮必须由前面的选择挣来，避免口号、连续感叹、空泛宣言和旁白替人物燃情。"),
    ToneConfig("erotic", "色情",
               "从关系变化、边界试探、身体距离和具体反应逐步积累张力；保持人物目的与感受连续，避免堆砌暧昧形容词、重复身体反应或用含混华丽的套话代替现场。"),
]

# ========== 查询函数 ==========

_genre_by_display: dict[str, GenreConfig] = {g.display_name: g for g in GENRES}
_genre_by_key: dict[str, GenreConfig] = {g.key: g for g in GENRES}
_tone_by_display: dict[str, ToneConfig] = {t.display_name: t for t in STYLE_TONES}
_tone_by_key: dict[str, ToneConfig] = {t.key: t for t in STYLE_TONES}

GENRE_DISPLAY_NAMES: list[str] = [g.display_name for g in GENRES]
TONE_DISPLAY_NAMES: list[str] = [t.display_name for t in STYLE_TONES]


def get_genre_by_display(name: str) -> GenreConfig | None:
    return _genre_by_display.get(name)


def get_genre_by_key(key: str) -> GenreConfig | None:
    return _genre_by_key.get(key)


def get_tone_by_display(name: str) -> ToneConfig | None:
    return _tone_by_display.get(name)


def get_tone_by_key(key: str) -> ToneConfig | None:
    return _tone_by_key.get(key)


def get_genre_display(key: str) -> str:
    """根据 key 返回显示名，未找到返回空字符串"""
    cfg = get_genre_by_key(key)
    return cfg.display_name if cfg else ""


def get_tone_display(key: str) -> str:
    """根据 key 返回显示名，未找到返回空字符串"""
    cfg = get_tone_by_key(key)
    return cfg.display_name if cfg else ""
