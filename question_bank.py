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


def lookup_question(question_html: str, current_options: list[dict]) -> str | None:
    """
    根据题目文本在题库中查找已知答案，自动适配选项顺序变化。
    原理：题库存的是"正确答案的文本内容"，返回时映射到当前页面的选项字母。
    例如题库存 answer="B"，options=["A.CO","B.NH3","C.O2"]，
    如果当前页面选项是 ["A.O2","B.CO","C.NH3"]，则返回 "C"（因为 NH3 现在是 C）。
    """
    q_hash = _hash_text(question_html)
    bank = load_bank()
    entry = bank.get("questions", {}).get(q_hash)
    if not entry or not entry.get("answer"):
        return None

    # 如果答案是待定状态（无置信度），不返回
    if entry.get("source") == "pending" and not entry.get("answer"):
        return None

    stored_answer = entry["answer"]  # 例如 "B" 或 "ABD" (多选)
    stored_options = entry.get("options", [])  # 例如 ["A. CO", "B. NH3", "C. O2"]

    # 提取当前页面选项的纯文本
    def _opt_content(opt):
        """提取选项的纯文本内容"""
        if isinstance(opt, dict):
            return re.sub(r"<[^>]+>", "", opt.get("wfLineContent", opt.get("text", ""))).strip()
        return str(opt).strip()

    current_texts = [_opt_content(o) for o in current_options]

    # 对每个存储的答案字母，找到对应的选项文本，再在当前选项中匹配
    result_letters = []
    for letter in stored_answer.upper():
        if letter < 'A' or letter > 'F':
            result_letters.append(letter)
            continue
        # 从存储选项中找这个字母对应的文本
        stored_idx = ord(letter) - ord('A')
        stored_text = ""
        if stored_idx < len(stored_options):
            # 提取存储选项的纯文本（去掉 "A. " 前缀）
            stored_text = stored_options[stored_idx]
            # 去掉选项字母前缀，如 "A. CO" → "CO"
            stored_text = re.sub(r"^[A-F][\.\、]\s*", "", stored_text).strip()

        # 在当前选项中找匹配文本
        matched_letter = None
        for ci, ct in enumerate(current_texts):
            # 去掉选项字母前缀后比较
            ct_clean = re.sub(r"^[A-F][\.\、]\s*", "", ct).strip()
            if ct_clean and stored_text and ct_clean == stored_text:
                matched_letter = chr(ord('A') + ci)
                break

        if matched_letter:
            result_letters.append(matched_letter)
        else:
            # 文本匹配失败，回退到按字母顺序（选项顺序没变的情况）
            if stored_idx < len(current_options):
                result_letters.append(chr(ord('A') + stored_idx))

    return "".join(result_letters)


# 来源置信度：数字越高越可信
SOURCE_CONFIDENCE = {
    "submitAnswer": 100,  # 平台判定，绝对正确
    "deepseek-v4-pro": 80,
    "deepseek-reasoner": 65,
    "deepseek-chat": 40,
    "deepseek": 35,       # 旧默认源
    "pending": 0,         # 待定
}


def record_answer(question_html: str, options: list[dict],
                  correct_answer: str = "", source: str = "submitAnswer") -> str:
    """
    记录一道题到题库。只有在置信度 >= 已有记录时才更新答案。
    这样平台判定 > V4 Pro > R1 > V3，防止低置信度答案覆盖高置信度。
    """
    q_hash = _hash_text(question_html)
    bank = load_bank()
    is_new = q_hash not in bank.get("questions", {})

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
