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

# 需要加密存储的字段（deepseek_api_key 保留用于解密老版本配置文件）
_SENSITIVE_KEYS = ["password", "deepseek_api_key", "ai_key"]

DEFAULTS: dict = {
    # ---- 学习平台 ----
    "base_url": "http://aqhb.tlysyun.com:8000",
    "username": "",
    "password": "",
    # ---- 刷分策略 ----
    "strategy_order": ["article", "video", "exercise"],  # 图文 > 视频 > 每日答题
    "weekly_target": 30,
    # ---- AI 答题 API（OpenAI 兼容协议，支持多服务商）----
    "ai_provider": "deepseek",
    "ai_key": "",
    "ai_base_url": "https://api.deepseek.com",
    "ai_model": "deepseek-v4-flash",
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


# 服务商预设表：设置页下拉 + 检测接口共用（OpenAI 兼容协议）
AI_PROVIDERS = {
    "deepseek": {"label": "DeepSeek（深度求索）", "base_url": "https://api.deepseek.com", "model_hint": "deepseek-v4-flash"},
    "openai": {"label": "OpenAI", "base_url": "https://api.openai.com", "model_hint": "如 gpt-5-mini"},
    "moonshot": {"label": "Kimi（月之暗面）", "base_url": "https://api.moonshot.cn/v1", "model_hint": "如 kimi-k2"},
    "dashscope": {"label": "通义千问（DashScope）", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model_hint": "如 qwen3-max"},
    "zhipu": {"label": "智谱 GLM", "base_url": "https://open.bigmodel.cn/api/paas/v4", "model_hint": "如 glm-4.6"},
    "siliconflow": {"label": "硅基流动", "base_url": "https://api.siliconflow.cn/v1", "model_hint": "如 deepseek-ai/DeepSeek-V3.1"},
    "custom": {"label": "自定义（OpenAI 兼容）", "base_url": "", "model_hint": "输入模型 ID"},
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
            # 旧版 deepseek_* 键迁移到通用 ai_*（必须在解密之后、merge 之前）
            if "ai_provider" not in saved:
                saved["ai_provider"] = "deepseek"
                if "deepseek_api_key" in saved:
                    saved["ai_key"] = saved.pop("deepseek_api_key")
                if "deepseek_base_url" in saved:
                    saved["ai_base_url"] = saved.pop("deepseek_base_url")
                if "deepseek_model" in saved:
                    saved["ai_model"] = saved.pop("deepseek_model")
            # 清理残留旧键，防止下次保存时写回 config.json
            for _old_key in ("deepseek_api_key", "deepseek_base_url", "deepseek_model"):
                saved.pop(_old_key, None)
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
