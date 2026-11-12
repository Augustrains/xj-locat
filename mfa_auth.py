"""学校 CAS 原生二次验证及 GitHub 验证码回复加密协议。"""

import base64
import json
from urllib.parse import urlparse

from Crypto.Cipher import PKCS1_OAEP
from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA


class MfaError(RuntimeError):
    pass


def _data(response, operation):
    response.raise_for_status()
    body = response.json()
    if body.get("code") != 0 or not isinstance(body.get("data") or {}, dict):
        raise MfaError(f"{operation}失败；请检查学校验证页面，不自动重试发送。")
    return body.get("data") or {}


def start_challenge(session, state, channel, cas_origin="https://login.xjtu.edu.cn/cas"):
    if channel not in ("securephone", "secureemail"):
        raise MfaError("仅支持学校的安全手机和安全邮箱验证码。")
    data = _data(session.get(
        f"{cas_origin}/mfa/initByType/{channel}",
        params={"state": state}, timeout=30,
    ), "初始化二次验证")
    server = data.get("attestServerUrl", "").rstrip("/")
    parsed = urlparse(server)
    if (parsed.scheme != "https" or not parsed.hostname
            or not (parsed.hostname == "xjtu.edu.cn" or parsed.hostname.endswith(".xjtu.edu.cn"))
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise MfaError("学校返回了非预期的验证服务器，已停止。")
    contact = data.get("securePhone" if channel == "securephone" else "secureEmail")
    if not contact or not data.get("gid"):
        raise MfaError("该账号未提供可用的所选安全手机/邮箱验证方式。")
    challenge = {"server": server, "gid": data["gid"], "channel": channel}
    _data(session.post(
        f"{server}/api/guard/{channel}/send",
        json={"gid": challenge["gid"]}, timeout=30,
    ), "发送验证码")
    return challenge


def validate_challenge(session, challenge, code):
    if not isinstance(code, str) or not code.isascii() or not code.isdigit() or not 4 <= len(code) <= 10:
        raise MfaError("验证码格式无效。")
    data = _data(session.post(
        f"{challenge['server']}/api/guard/{challenge['channel']}/valid",
        json={"gid": challenge["gid"], "code": code}, timeout=30,
    ), "提交验证码")
    if data.get("status") != 2:
        raise MfaError("学校未确认验证成功，验证码可能错误或过期。")


def new_reply_key():
    key = RSA.generate(2048)
    return key.export_key().decode("ascii"), key.public_key().export_key().decode("ascii")


def encrypt_reply(public_key, run_id, challenge_id, code, trust_client=False):
    body = json.dumps({"run_id": str(run_id), "challenge_id": challenge_id,
                       "code": code, "trust_client": bool(trust_client)}, separators=(",", ":")).encode()
    encrypted = PKCS1_OAEP.new(RSA.import_key(public_key), hashAlgo=SHA256).encrypt(body)
    return base64.b64encode(encrypted).decode("ascii")


def decrypt_reply(private_key, encrypted_code, run_id, challenge_id):
    try:
        encrypted = base64.b64decode(encrypted_code, validate=True)
        body = json.loads(PKCS1_OAEP.new(RSA.import_key(private_key), hashAlgo=SHA256).decrypt(encrypted))
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise MfaError("验证码回复无法解密。") from None
    if not isinstance(body, dict) or body.get("run_id") != str(run_id) or body.get("challenge_id") != challenge_id:
        raise MfaError("验证码回复不属于本次登录。")
    code = body.get("code")
    if not isinstance(code, str) or not code.isascii() or not code.isdigit() or not 4 <= len(code) <= 10:
        raise MfaError("验证码回复格式无效。")
    if not isinstance(body.get("trust_client"), bool):
        raise MfaError("可信客户端选择无效。")
    return body
