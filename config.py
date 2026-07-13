"""
配置管理模块 —— 读取/保存用户配置，敏感字段自动加解密
"""
import json
import os
import sys
from crypto_utils import encrypt, decrypt

# PyInstaller 打包后数据存到 exe 同目录，避免写入临时文件夹
if getattr(sys, 'frozen', False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(_BASE_DIR, "config.json")

# 需要加密存储的字段
_SENSITIVE_KEYS = ["password", "deepseek_api_key"]

DEFAULTS: dict = {
    # ---- 学习平台 ----
    "base_url": "http://aqhb.tlysyun.com:8000",
    "username": "",
    "password": "",
    # ---- 刷分策略 ----
    "strategy_order": ["article", "video", "exercise"],  # 图文 > 视频 > 每日答题
    "weekly_target": 30,
    "jituan_priority": ["article", "video"],  # 集团课程子tab优先级: article=专业课程(图文), video=视频课程
    # ---- DeepSeek API ----
    "deepseek_api_key": "",
    "deepseek_base_url": "https://api.deepseek.com",
    "deepseek_model": "deepseek-chat",  # deepseek-chat(V3) / deepseek-reasoner(R1)
    # ---- 浏览器 ----
    "headless": False,          # 是否无头运行
    "browser_slow_mo": 300,     # 操作间隔(ms)，防反爬
    # ---- UI 主题 ----
    "theme": {
        "accent": "#2563eb",        # 主色调（蓝）
        "bg": "#f6f7f9",            # 背景色
        "text": "#1c1f26",          # 文字色
        "muted": "#5b626d",         # 次要文字
        "surface": "#ffffff",       # 卡片背景
        "border": "#e5e7eb",        # 边框色
    },
}


def load_config() -> dict:
    """加载配置，自动解密敏感字段"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            # 解密敏感字段
            for key in _SENSITIVE_KEYS:
                if key in saved and isinstance(saved[key], str) and saved[key]:
                    val = saved[key]
                    if val.startswith("OW:"):
                        # 新版加密
                        saved[key] = decrypt(val)
                    elif len(val) > 40 and all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=_-" for c in val):
                        # 旧版无标记加密数据 → 已无法解密，清空重填
                        saved[key] = ""
                    # 短明文（如 "abc123"）→ 保留，下次保存时加密
            return _deep_merge(DEFAULTS, saved)
        except (json.JSONDecodeError, IOError):
            pass
    return dict(DEFAULTS)


def save_config(cfg: dict) -> None:
    """保存配置，自动加密敏感字段"""
    to_save = dict(cfg)
    for key in _SENSITIVE_KEYS:
        if key in to_save and isinstance(to_save[key], str) and to_save[key]:
            to_save[key] = encrypt(to_save[key])
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(to_save, f, ensure_ascii=False, indent=2)


def _deep_merge(base: dict, override: dict) -> dict:
    """深度合并字典，override 覆盖 base"""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
