"""
敏感字段加解密 —— XOR + base64，防止 config.json 明文泄露
密钥由本机机器名派生，换机拷贝不会泄露
"""
import base64
import hashlib
import platform


def _derive_key() -> bytes:
    """从本机特征派生 32 字节密钥"""
    seed = f"{platform.node()}-{platform.machine()}-OverWall-2026"
    return hashlib.sha256(seed.encode("utf-8")).digest()


_MARKER = "OW:"

def encrypt(plaintext: str) -> str:
    """加密字符串，返回带标记的 base64 密文"""
    if not plaintext:
        return ""
    key = _derive_key()
    data = plaintext.encode("utf-8")
    enc = bytes(data[i] ^ key[i % len(key)] for i in range(len(data)))
    return _MARKER + base64.urlsafe_b64encode(enc).decode()


def decrypt(ciphertext: str) -> str:
    """解密字符串，返回明文；非加密值直接返回原值"""
    if not ciphertext:
        return ""
    # 没有标记 = 旧明文，直接返回
    if not ciphertext.startswith(_MARKER):
        return ciphertext
    try:
        key = _derive_key()
        data = base64.urlsafe_b64decode(ciphertext[len(_MARKER):])
        dec = bytes(data[i] ^ key[i % len(key)] for i in range(len(data)))
        return dec.decode("utf-8")
    except Exception:
        return ciphertext  # 解析失败返回原值
