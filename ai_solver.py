"""
DeepSeek AI 答题模块 —— 支持选择题和填空题
"""
import json
import re
import requests


def _strip(html: str) -> str:
    """去除 HTML 标签"""
    return re.sub(r"<[^>]+>", "", html or "").strip()


def query_deepseek(question_html: str, options: list[dict], api_key: str,
                   base_url: str = "https://api.deepseek.com",
                   question_type: str = "单选题",
                   model: str = "deepseek-chat") -> str | None:
    """
    调用 DeepSeek API 获取答案（选择题）
    返回: 选项字母 "A"~"F"，失败返回 None
    """
    if not api_key:
        return None

    question_text = _strip(question_html)
    option_lines = []
    for opt in options:
        label = opt.get("wfLineOption", "?")
        content = _strip(opt.get("wfLineContent", ""))
        option_lines.append(f"{label}. {content}")

    # 根据题型调整提示
    if "多选" in (question_type or ""):
        answer_hint = "多选题。选出所有正确选项。只回复字母组合如 ACD。"
    elif "判断" in (question_type or ""):
        answer_hint = "判断题目说法是否正确。正确回复A，错误回复B。不要解释，不要多余文字。"
    else:
        answer_hint = "选出唯一正确的选项。只需回复正确选项的字母（A/B/C/D/E/F中的一个），不要解释，不要多余文字。"

    prompt = (
        f"你是一个煤矿安全培训考试助手。题型：{question_type}。\n"
        f"{answer_hint}\n\n"
        f"题目：{question_text}\n\n"
        f"选项：\n{chr(10).join(option_lines)}\n\n"
        "答案："
    )

    try:
        is_multi = "多选" in (question_type or "")
        msgs = [{"role": "user", "content": prompt}]
        if not is_multi:
            msgs.insert(0, {"role": "system", "content": "你是煤矿安全考试助手，只回复正确选项字母。"})
        payload = {
            "model": model,
            "messages": msgs,
            "max_tokens": 800 if is_multi else 100,
            "temperature": 0.0,
        }
        # V4 Pro 多选时不加 thinking（可能导致空响应）
        if "v4" in model or "pro" in model.lower():
            if not is_multi:
                payload["reasoning_effort"] = "high"
                payload["extra_body"] = {"thinking": {"type": "enabled"}}

        resp = requests.post(
            f"{base_url.rstrip('/')}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=60 if is_multi else 30,
        )
        data = resp.json()

        # 提取 content：优先 content，其次 reasoning_content
        msg = data.get("choices", [{}])[0].get("message", {})
        content = msg.get("content", "") or ""
        reasoning = msg.get("reasoning_content", "") or ""

        # V4/Pro 模型开启 thinking 后，content 可能为空，答案在 reasoning 里
        # 单选和多选都需要从 reasoning_content 兜底提取
        if (not content) and reasoning:
            import re as _re2
            m = _re2.search(r"答案[是应为]+?\s*([A-F]{1,6})", reasoning)
            if m:
                content = m.group(1)
            elif is_multi:
                # 多选：找推理中最后出现的字母组合（2-6个连续字母）
                letters = _re2.findall(r"[A-F]{2,6}", reasoning)
                if letters:
                    content = letters[-1]
            else:
                # 单选：找推理中首次出现的单个大写A-F字母
                single = _re2.findall(r"\b([A-F])\b", reasoning)
                if single:
                    content = single[0]

        if not content and is_multi:
            # 重试一次
            import time as _time
            _time.sleep(3)
            resp2 = requests.post(
                f"{base_url.rstrip('/')}/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload, timeout=90,
            )
            data2 = resp2.json()
            msg2 = data2.get("choices", [{}])[0].get("message", {})
            content = msg2.get("content", "") or ""
            reasoning2 = msg2.get("reasoning_content", "") or ""
            if not content and reasoning2:
                content = reasoning2

        if not content:
            return None

        # 提取选项字母：找所有连续的大写A-F
        text = content.strip().upper()
        matches = re.findall(r"[A-F]+", text)
        if matches:
            # 取最长的匹配（多选通常是连续字母组合）
            return max(matches, key=len)
        return None
    except Exception:
        return None


def query_deepseek_fill(question_html: str, blanks_count: int, api_key: str,
                        base_url: str = "https://api.deepseek.com",
                        question_type: str = "填空题",
                        model: str = "deepseek-chat") -> list[str]:
    """
    调用 DeepSeek API 解答填空题
    返回: 每个空的答案列表 ["答案1", "答案2", ...]
    """
    if not api_key or blanks_count <= 0:
        return []

    question_text = _strip(question_html)

    # 根据题型调整提示
    if "简答" in (question_type or ""):
        hint = "用简洁的语言回答，控制在50字以内，直接给出答案。不要解释，不要多余文字。"
    elif blanks_count == 1:
        hint = "只回复答案文本（一个词或短语），不要解释，不要多余文字。"
    else:
        hint = f"按顺序回复{blanks_count}个空的答案，用 || 分隔。例如：答案1||答案2。不要解释，不要多余文字。"

    prompt = (
        f"你是一个煤矿安全培训考试助手。题型：{question_type}{'（' + str(blanks_count) + '空）' if '简答' not in (question_type or '') else ''}。\n"
        f"{hint}\n\n"
        f"题目：{question_text}\n\n"
        "答案："
    )

    try:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "你是一个精准的煤矿安全考试答题助手。"},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 1500 if "简答" in (question_type or "") else 800,
            "temperature": 0.0,
        }
        if "v4" in model or "pro" in model.lower():
            payload["reasoning_effort"] = "high"
            payload["extra_body"] = {"thinking": {"type": "enabled"}}

        resp = requests.post(
            f"{base_url.rstrip('/')}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not content:
            return []
        content = content.strip()
        if "||" in content:
            return [s.strip() for s in content.split("||")]
        return [content]
    except Exception:
        return []


def extract_correct_from_response(submit_response: dict) -> str | None:
    """
    从 submitAnswer 接口响应中提取正确答案
    响应结构: {"data": {"trueAnswer": "A", "wfAnswer": "正确"}}
    """
    if not submit_response:
        return None
    data = submit_response.get("data", {})
    answer = data.get("trueAnswer")
    if answer:
        return str(answer).upper().strip()
    return None
