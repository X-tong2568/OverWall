"""
题库管理模块 —— JSON 文件存储，MD5 哈希匹配，支持精确匹配和手动维护
"""
import hashlib
import json
import os
import re
import sys

# PyInstaller 打包后数据存到 exe 同目录，避免写入临时文件夹
if getattr(sys, 'frozen', False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))

BANK_FILE = os.path.join(_BASE_DIR, "question_bank.json")


def _hash_text(text: str) -> str:
    """对题目文本做 MD5 哈希，用作题库 key"""
    # 标准化：去HTML标签、去空白、小写
    clean = re.sub(r"<[^>]+>", "", text)
    clean = re.sub(r"\s+", "", clean).lower()
    return hashlib.md5(clean.encode("utf-8")).hexdigest()


def load_bank() -> dict:
    """加载题库"""
    if os.path.exists(BANK_FILE):
        try:
            with open(BANK_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "questions" in data:
                return data
        except (json.JSONDecodeError, IOError):
            pass
    return {"version": "1.0", "questions": {}}


def save_bank(bank: dict) -> None:
    """保存题库"""
    with open(BANK_FILE, "w", encoding="utf-8") as f:
        json.dump(bank, f, ensure_ascii=False, indent=2)


def split_option_text(text: str) -> tuple[str | None, str]:
    """
    拆解选项文本为 (平台显示字母, 纯内容)。
    兼容多层前缀：'A. [图标]\\nA、\\n\\n内容'（我们加的字母+平台图标+平台字母）、
    'A. A\\n\\n内容'、'A. 内容'、'内容'。
    字母后必须紧跟换行才视为标签，避免误伤以字母开头的内容。
    """
    t = (text or "").strip()
    # 1. 剥掉字母+点号前缀，最多 2 层（我们加的外层 + 平台自己的标签，如 'A. A.  正确'）。
    #    只认字母+点号：平台用顿号，若这里也认顿号会把 'B、错' 误当我们的前缀剥掉
    for _ in range(2):
        m = re.match(r"^([A-F])\s*\.\s+", t)
        if not m:
            break
        t = t[m.end():]
        # 层间可能夹平台图标字体和空白
        t = re.sub(r"^[\ue000-\uf8ff\s]+", "", t)
    # 2. 剥掉平台图标字体（私有区字符 \ue000-\uf8ff）和首部空白
    t = re.sub(r"^[\ue000-\uf8ff\s]+", "", t)
    # 3. 平台显示的字母标签：字母(+分隔符)+换行
    display_letter = None
    m2 = re.match(r"^([A-FＡ-Ｆ])\s*[\.\、\．:：]?\s*\n+", t)
    if m2:
        display_letter = m2.group(1).upper()
        t = t[m2.end():]
        t = re.sub(r"^[\ue000-\uf8ff\s]+", "", t)
    return display_letter, t.strip()


def letters_to_content(answer: str, options: list[dict], prefer_display: bool = False) -> str:
    """
    字母答案转成选项纯内容文本（多选用 '||' 连接，比较时拆集合即可）。
    prefer_display=True 时优先按平台显示的字母定位（用于平台 trueAnswer 的字母坐标系），
    找不到显示字母再按位置定位。无法还原时原样返回。
    """
    a = (answer or "").strip().upper()
    if not options or not re.fullmatch(r"[A-F]{1,6}", a):
        return answer
    contents = []
    for letter in a:
        target = None
        if prefer_display:
            for opt in options:
                if isinstance(opt, dict) and (opt.get("display_letter") or "").upper() == letter:
                    target = opt
                    break
        if target is None:
            idx = ord(letter) - ord("A")
            if idx >= len(options):
                return answer
            target = options[idx]
        if not isinstance(target, dict):
            return answer
        raw = target.get("wfLineContent", target.get("text", ""))
        _, content = split_option_text(raw)
        if not content:
            return answer
        contents.append(content)
    return "||".join(contents)


def _norm_content(text: str) -> str:
    """内容比较用归一化：去所有空白"""
    return re.sub(r"\s+", "", text or "")


def lookup_question(question_html: str, current_options: list[dict]) -> str | None:
    """
    根据题目文本在题库中查找已知答案。锚定的是选项内容不是字母序号：
    - 新记录存内容文本；旧记录存字母（用存储时的选项列表还原内容）
    - 内容与当前选项匹配后，返回当前的字母
    - 匹配不到返回 None（让 AI 现解，绝不按位置瞎猜——平台会打乱选项顺序）
    """
    q_hash = _hash_text(question_html)
    bank = load_bank()
    entry = bank.get("questions", {}).get(q_hash)
    if not entry or not entry.get("answer"):
        return None

    # 如果答案是待定状态（无置信度），不返回
    if entry.get("source") == "pending" and not entry.get("answer"):
        return None

    stored_answer = entry["answer"]
    stored_options = entry.get("options", [])

    def _opt_content(opt):
        """提取选项的原始文本"""
        if isinstance(opt, dict):
            return opt.get("wfLineContent", opt.get("text", ""))
        return str(opt)

    # 旧数据：答案还是字母 → 用存储时的选项列表还原出内容
    if re.fullmatch(r"[A-F]{1,6}", stored_answer.strip().upper()):
        targets = []
        for letter in stored_answer.strip().upper():
            idx = ord(letter) - ord("A")
            if idx >= len(stored_options):
                return None  # 旧数据缺选项，无法还原
            _, content = split_option_text(stored_options[idx])
            if not content:
                return None
            targets.append(content)
    else:
        # 新数据：答案就是内容（多选 '||' 连接）
        targets = [t for t in stored_answer.split("||") if t.strip()]

    # 解析当前选项并与内容归一化匹配
    current_parsed = [split_option_text(_opt_content(o)) for o in current_options]
    result = []
    for target in targets:
        t_norm = _norm_content(target)
        if not t_norm:
            continue
        matched = None
        for i, (_, ct) in enumerate(current_parsed):
            if ct and _norm_content(ct) == t_norm:
                matched = chr(ord("A") + i)
                break
        if matched:
            result.append(matched)
        # 匹配失败直接放弃该内容，不按位置猜
    return "".join(result) if result else None


# 来源置信度：数字越高越可信
# deepseek* 键保留：老题库里存的 source 字符串需要继续匹配
# 多服务商后 source 是各家的模型名，查不到时默认 20（不会覆盖已有高置信度答案）
SOURCE_CONFIDENCE = {
    "submitAnswer": 100,  # 平台判定，绝对正确
    "deepseek-v4-pro": 80,
    "deepseek-v4-flash": 45,
    "deepseek": 35,       # 旧默认源
    "pending": 0,         # 待定
}


def record_answer(question_html: str, options: list[dict],
                  correct_answer: str = "", source: str = "submitAnswer") -> str:
    """
    记录一道题到题库。只有在置信度 >= 已有记录时才更新答案。
    这样平台判定 > V4 Pro > V4 Flash > 未知来源(20)，防止低置信度答案覆盖高置信度。
    答案入库前先做内容锚定：字母 → 选项纯内容（平台会打乱选项顺序，字母不可靠）。
    """
    q_hash = _hash_text(question_html)
    bank = load_bank()
    is_new = q_hash not in bank.get("questions", {})

    # 内容锚定：字母答案转成选项内容文本，已内容则原样（多选 '||' 连接）
    if correct_answer and options:
        correct_answer = letters_to_content(correct_answer, options)

    option_texts = []
    for opt in options:
        label = opt.get("wfLineOption", "?")
        content = re.sub(r"<[^>]+>", "", opt.get("wfLineContent", "")).strip()
        option_texts.append(f"{label}. {content}")

    from datetime import date
    existing = bank["questions"].get(q_hash, {})

    new_confidence = SOURCE_CONFIDENCE.get(source, 20)
    old_source = existing.get("source", "pending")
    old_confidence = SOURCE_CONFIDENCE.get(old_source, 0)

    # 只有置信度 >= 旧答案时才更新
    if is_new or new_confidence >= old_confidence or not existing.get("answer"):
        bank["questions"][q_hash] = {
            "question": re.sub(r"<[^>]+>", "", question_html).strip(),
            "options": option_texts,
            "answer": correct_answer or existing.get("answer", ""),
            "source": source if correct_answer else existing.get("source", "pending"),
            "created_at": existing.get("created_at", "") or str(date.today()),
        }
        save_bank(bank)

    return q_hash


def bank_stats() -> dict:
    """返回题库统计信息"""
    bank = load_bank()
    questions = bank.get("questions", {})
    sources = {}
    for q in questions.values():
        src = q.get("source", "unknown")
        sources[src] = sources.get(src, 0) + 1
    return {
        "total": len(questions),
        "by_source": sources,
    }
