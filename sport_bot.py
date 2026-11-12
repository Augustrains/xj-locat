#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
XJTU 运动打卡自动化脚本 (兼容 Clash/代理环境版)

配置从本地 .env 或环境变量加载。
所有学校接口请求使用 trust_env=False 的 Session 直连。
支持仅检查配置或登录，不执行签到、签退。
"""

import requests
import logging
import time
import base64
import os
import json
import smtplib
import shutil
import subprocess
import argparse
import re
from enum import Enum
from pathlib import Path
from email.mime.text import MIMEText
from email.header import Header
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs, urljoin
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5


# -------------------- 配置类 --------------------
class Config:
    """
    程序配置类
    """

    # ========== 用户信息配置 ==========
    USER = ""  # 从本地 .env 或环境变量加载
    PASSWORD = ""  # 从本地 .env 或环境变量加载
    CLIENT_ID = ""  # 当前脚本客户端的随机标识；验证成功后可由用户选择信任
    TRUST_CLIENT = False  # 只有用户明确同意后才启用学校的可信客户端选项

    # ========== 地理位置配置 ==========
    LONGITUDE = 108.654387  # 经度
    LATITUDE = 34.257229   # 纬度

    # ========== 邮件通知配置 ==========
    SEND_EMAIL = False  # 配置完整后在 .env 中启用
    SMTP_AUTH_CODE = ""
    EMAIL_SENDER = ""
    EMAIL_RECEIVER = ""

    # ========== Ubuntu桌面通知配置 ==========
    SEND_DESKTOP_NOTIFICATION = True  # 打卡失败时弹出Ubuntu桌面通知

    # ========== 日志配置 ==========
    LOG_FILE = os.path.join(os.path.dirname(__file__), "sport_bot.log")
    LOG_LEVEL = logging.INFO

    # ========== 系统配置 ==========
    APP_ID = "1740"
    REDIR = "https://ipahw.xjtu.edu.cn/sso/callback"
    STATE = "1234"
    UA = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
    )

    # API端点
    OPEN_OAUTH = "https://org.xjtu.edu.cn/openplatform/oauth/authorize"
    CAS_PUBKEY = "https://login.xjtu.edu.cn/cas/jwt/publicKey"
    MFA_DETECT = "https://login.xjtu.edu.cn/cas/mfa/detect"
    CODE_LOGIN = "https://ipahw.xjtu.edu.cn/szjy-boot/sso/codeLogin"
    SIGN_IN_URL = "https://ipahw.xjtu.edu.cn/szjy-boot/api/v1/sportActa/signRun"
    SIGN_OUT_URL = "https://ipahw.xjtu.edu.cn/szjy-boot/api/v1/sportActa/signOutTrain"


# -------------------- 初始化日志 --------------------
class RedactSecretsFilter(logging.Filter):
    """过滤公开 Actions 日志中的配置凭证和认证 URL 参数。"""

    def filter(self, record):
        message = record.getMessage()
        for secret in sorted(
            (Config.USER, Config.PASSWORD, Config.SMTP_AUTH_CODE, Config.CLIENT_ID),
            key=len, reverse=True,
        ):
            if secret:
                message = message.replace(secret, "[REDACTED]")
        message = re.sub(
            r"(?i)([?&](?:token|access_token|refresh_token|id_token|code|ticket|password)=)[^&\s\"'<>]+",
            r"\1[REDACTED]", message,
        )
        record.msg = message
        record.args = ()
        return True


def setup_logging():
    logger = logging.getLogger()
    logger.setLevel(Config.LOG_LEVEL)
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    file_handler = logging.FileHandler(Config.LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.addFilter(RedactSecretsFilter())
    logger.addHandler(file_handler)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(RedactSecretsFilter())
    logger.addHandler(console_handler)

def load_config():
    """加载项目目录下的 .env；进程环境变量优先，不执行文件内容。"""
    values = {}
    env_file = Path(__file__).resolve().parent / ".env"
    if env_file.exists():
        for number, raw in enumerate(env_file.read_text(encoding="utf-8").splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            if not separator or not key.strip().startswith("SPORT_"):
                raise ValueError(f".env 第 {number} 行格式错误")
            value = value.strip()
            if value.startswith('"'):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    raise ValueError(f".env 第 {number} 行引号或转义格式错误") from None
                if not isinstance(value, str):
                    raise ValueError(f".env 第 {number} 行必须填写字符串")
            elif value.startswith("'"):
                if len(value) < 2 or not value.endswith("'"):
                    raise ValueError(f".env 第 {number} 行引号未闭合")
                value = value[1:-1]
            values[key.strip()] = value
    for name in ("USER", "PASSWORD", "CLIENT_ID", "TRUST_CLIENT", "LONGITUDE", "LATITUDE", "SEND_EMAIL",
                 "SMTP_AUTH_CODE", "EMAIL_SENDER", "EMAIL_RECEIVER",
                 "SEND_DESKTOP_NOTIFICATION"):
        key = f"SPORT_{name}"
        value = os.environ.get(key, values.get(key, getattr(Config, name)))
        if name in ("SEND_EMAIL", "SEND_DESKTOP_NOTIFICATION", "TRUST_CLIENT"):
            normalized = str(value).lower()
            if normalized not in ("true", "false", "1", "0", "yes", "no"):
                raise ValueError(f"{key} 必须为 true 或 false")
            value = normalized in ("true", "1", "yes")
        elif name in ("LONGITUDE", "LATITUDE"):
            try:
                value = float(value)
            except (ValueError, TypeError):
                raise ValueError(f"{key} 必须为数字") from None
        setattr(Config, name, value)
    if not Config.USER.strip() or not Config.PASSWORD:
        raise ValueError("请在 .env 中填写 SPORT_USER 和 SPORT_PASSWORD")
    if not -180 <= Config.LONGITUDE <= 180 or not -90 <= Config.LATITUDE <= 90:
        raise ValueError("经纬度超出有效范围")
    if Config.SEND_EMAIL and (
        not Config.SMTP_AUTH_CODE or "@" not in Config.EMAIL_SENDER
        or "@" not in Config.EMAIL_RECEIVER
    ):
        raise ValueError("启用邮件前请填写 SMTP 授权码、发件邮箱和收件邮箱")


# -------------------- 工具函数 --------------------
def rsa_encrypt(pwd: str, pem: str) -> str:
    try:
        cipher = PKCS1_v1_5.new(RSA.import_key(pem))
        encrypted = base64.b64encode(cipher.encrypt(pwd.encode())).decode()
        return "__RSA__" + encrypted
    except Exception as e:
        logging.error(f"密码加密失败: {str(e)}")
        raise

def get_form(html: str, form_selector: str = "form#fm1"):
    soup = BeautifulSoup(html, "html.parser")
    form = soup.select_one(form_selector) or soup.select_one("form")
    if not form:
        raise ValueError(f"未找到表单，选择器: {form_selector}")
    return form

def extract_form_inputs(html: str, form_selector: str = "form#fm1") -> dict:
    try:
        form = get_form(html, form_selector)
        data = {}
        for inp in form.select("input[name]"):
            if inp.get("type") == "checkbox": continue
            name = inp.get("name")
            value = inp.get("value", "")
            if name: data[name] = value
        return data
    except Exception as e:
        logging.error(f"表单提取失败: {str(e)}")
        raise

def extract_form_action(html: str, page_url: str, form_selector: str = "form#fm1") -> str:
    form = get_form(html, form_selector)
    action = form.get("action") or page_url
    return urljoin(page_url, action)

def mfa_detect(session: requests.Session, username: str, encrypted_password: str, fp_visitor_id: str, cas_login_url: str) -> dict:
    logging.info("执行MFA检测")
    mfa_data = {
        "loginType": "passwordLogin",
        "username": username,
        "password": encrypted_password,
        "fpVisitorId": fp_visitor_id,
    }
    try:
        response = session.post(
            Config.MFA_DETECT,
            data=mfa_data,
            headers={
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Referer": cas_login_url,
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=10,
        )
        if response.status_code == 200:
            return response.json()
        return None
    except Exception as e:
        logging.error(f"MFA检测异常: {e}")
        return None

def diagnose_login_response(html: str) -> str:
    if "当前登录环境异常" in html or "安全验证" in html:
        return "CAS要求额外安全验证，请先在浏览器手动登录并设为可信客户端，或补充短信/安全手机验证流程。"
    if "验证码" in html:
        return "触发了验证码，请先在浏览器中手动登录一次释放限制。"
    for keyword in ("用户名或密码错误", "密码错误", "账号或密码错误", "Invalid credentials"):
        if keyword in html:
            return "登录失败，请检查学号和密码是否正确。"
    return f"响应内容片段: {html[:200]}"

def prepare_login(user, password):
    """建立原始登录会话，保留提交表单与 MFA 检测结果。"""
    sess = requests.Session()
    sess.trust_env = False
    sess.headers.update({"User-Agent": Config.UA})
    try:
        r0 = sess.get(Config.OPEN_OAUTH, params={
            "appId": Config.APP_ID, "redirectUri": Config.REDIR,
            "responseType": "code", "scope": "user_info", "state": Config.STATE,
        }, allow_redirects=False, timeout=30)
        if "Location" not in r0.headers:
            raise RuntimeError(f"OAuth 未返回跳转地址，HTTP {r0.status_code}")
        page = sess.get(urljoin(r0.url, r0.headers["Location"]), timeout=30)
        page.raise_for_status()
        form = extract_form_inputs(page.text)
        form["fpVisitorId"] = Config.CLIENT_ID or form.get("fpVisitorId", "")
        public_key = sess.get(Config.CAS_PUBKEY, timeout=30)
        public_key.raise_for_status()
        encrypted_password = rsa_encrypt(password, public_key.text)
        form.update({"username": user, "password": encrypted_password})
        result = mfa_detect(sess, user, encrypted_password, form["fpVisitorId"], page.url)
        if not result or result.get("code") != 0 or not isinstance(result.get("data"), dict):
            raise RuntimeError("MFA 检测失败，请检查学校认证要求")
        data = result["data"]
        if not isinstance(data.get("need"), bool):
            raise RuntimeError("学校返回了无法识别的 MFA 状态")
        if data.get("state"):
            form["mfaState"] = data["state"]
        logging.info("MFA 检测成功，需额外验证: %s", data["need"])
        return {"session": sess, "form_data": form,
                "login_post_url": extract_form_action(page.text, page.url),
                "cas_login_url": page.url, "mfa_required": data["need"]}
    except Exception:
        sess.close()
        raise


def finish_login(context):
    """在相同会话中完成已通过所需验证的原始登录表单。"""
    sess = context["session"]
    form = dict(context["form_data"])
    if not form.get("trustAgent"):
        form["trustAgent"] = "true" if Config.TRUST_CLIENT else "false"
    response = sess.post(context["login_post_url"], data=form,
                         headers={"Referer": context["cas_login_url"]},
                         allow_redirects=False, timeout=30)
    if response.status_code != 302 or "Location" not in response.headers:
        raise RuntimeError(f"登录提交未完成，HTTP {response.status_code}；{diagnose_login_response(response.text)}")
    final = sess.get(urljoin(context["login_post_url"], response.headers["Location"]), timeout=30)
    final.raise_for_status()
    query = parse_qs(urlparse(final.url).query)
    if not query:
        raise RuntimeError("回调地址解析失败，登录流程未完成")
    token_response = sess.get(Config.CODE_LOGIN, headers={"Referer": final.url},
                              params={key: values[0] for key, values in query.items()}, timeout=30)
    token_response.raise_for_status()
    result = token_response.json()
    token = (result.get("data") or {}).get("token")
    if not token:
        raise RuntimeError("学校业务系统未返回访问令牌")
    return token


def get_token(user, password):
    context = None
    try:
        context = prepare_login(user, password)
        if context["mfa_required"]:
            logging.error("CAS 要求额外安全验证。请手动运行 GitHub 工作流并启用 request_mfa，由本人完成验证码验证。")
            return None
        return finish_login(context)
    except Exception as error:
        logging.error("获取访问令牌失败: %s", error)
        return None
    finally:
        if context:
            context["session"].close()


class SignResult(Enum):
    SUCCESS = "success"
    ALREADY_COMPLETED = "already_completed"
    FAILED = "failed"


def sign_operation(url: str, payload: dict, token: str, operation_name: str) -> SignResult:
    """
    执行签到或签退
    """
    logging.info(f"开始执行{operation_name}操作")
    # trust_env=False 同时屏蔽 HTTP_PROXY、HTTPS_PROXY 和 ALL_PROXY。
    session = requests.Session()
    session.trust_env = False
    
    try:
        response = session.post(
            url,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_4 like Mac OS X) AppleWebKit/537.36 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/537.36",
                "token": token,
            },
            data=json.dumps(payload),
            timeout=30,
        )

        if response.status_code == 200:
            res_data = response.json()
            if res_data.get("success"):
                logging.info(f"{operation_name}成功: {res_data.get('msg', '')}")
                return SignResult.SUCCESS
            elif (
                url == Config.SIGN_IN_URL
                and str(res_data.get("msg", "")).strip().rstrip("。！! ")
                == "你今天已经获得了锻炼积分，请明天继续"
            ):
                logging.info("学校系统确认今天已获得锻炼积分，本次无需重复打卡")
                return SignResult.ALREADY_COMPLETED
            else:
                logging.warning(f"{operation_name}失败: {res_data.get('msg', '未知原因')}")
                return SignResult.FAILED
        else:
            logging.error(f"{operation_name}请求异常，状态码: {response.status_code}")
            return SignResult.FAILED
    except Exception as e:
        logging.error(f"{operation_name}过程报错: {str(e)}")
        return SignResult.FAILED
    finally:
        session.close()


def send_email(content: str) -> bool:
    if not Config.SEND_EMAIL: return True
    try:
        msg = MIMEText(content, "plain", "utf-8")
        msg["From"] = Header(Config.EMAIL_SENDER)
        msg["To"] = Header(Config.EMAIL_RECEIVER)
        msg["Subject"] = Header("XJTU运动打卡通知", "utf-8")
        with smtplib.SMTP_SSL("smtp.qq.com", 465, timeout=30) as smtp:
            smtp.login(Config.EMAIL_SENDER, Config.SMTP_AUTH_CODE)
            smtp.sendmail(Config.EMAIL_SENDER, [Config.EMAIL_RECEIVER], msg.as_string())
        return True
    except Exception as e:
        logging.error(f"邮件发送失败: {e}")
        return False


def send_desktop_notification(title: str, content: str, urgency: str = "normal") -> bool:
    if not Config.SEND_DESKTOP_NOTIFICATION:
        return True

    notify_send = shutil.which("notify-send")
    if not notify_send:
        logging.warning("桌面通知发送失败：未找到 notify-send")
        return False

    env = os.environ.copy()
    runtime_dir = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    bus_path = os.path.join(runtime_dir, "bus")
    if os.path.isdir(runtime_dir):
        env.setdefault("XDG_RUNTIME_DIR", runtime_dir)
    if os.path.exists(bus_path):
        env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={bus_path}")

    try:
        subprocess.run(
            [notify_send, "-a", "sport-bot", "-u", urgency, title, content],
            check=False,
            timeout=5,
            env=env,
        )
        return True
    except Exception as e:
        logging.error(f"桌面通知发送失败：{e}")
        return False


def notify_failure(content: str) -> None:
    message = f"{content}\n日志文件：{Config.LOG_FILE}"
    send_desktop_notification("XJTU运动打卡失败", message, urgency="critical")


def main(token=None) -> int:
    logging.info("=" * 50)
    logging.info("XJTU运动打卡程序启动")
    logging.info("=" * 50)

    try:
        # 1. 获取Token
        if token is None:
            token = get_token(Config.USER, Config.PASSWORD)
        if not token:
            message = "无法获取Token，请检查账号密码或校内网连接"
            logging.error(message)
            notify_failure(message)
            send_email(f"打卡失败：{message}，请查看日志")
            return 1

        # 2. 签到
        sign_in_payload = {
            "sportType": 2,
            "longitude": Config.LONGITUDE,
            "latitude": Config.LATITUDE,
            "courseInfoId": "null",
        }
        sign_in_result = sign_operation(Config.SIGN_IN_URL, sign_in_payload, token, "签到")
        if sign_in_result is SignResult.ALREADY_COMPLETED:
            logging.info("今日已完成，跳过31分钟等待和签退，程序正常结束")
            return 0
        if sign_in_result is SignResult.SUCCESS:
            logging.info("签到成功，进入31分钟等待...")
            time.sleep(31 * 60) # 等待31分钟

            # 3. 签退
            sign_out_payload = {
                "longitude": Config.LONGITUDE,
                "latitude": Config.LATITUDE,
            }
            if sign_operation(Config.SIGN_OUT_URL, sign_out_payload, token, "签退") is SignResult.SUCCESS:
                send_email("打卡成功：签到签退均已完成")
                return 0
            else:
                message = "签到成功但签退失败，请手动检查"
                notify_failure(message)
                send_email(f"警告：{message}")
                return 1
        else:
            message = "签到环节报错，请查看日志"
            notify_failure(message)
            send_email(f"打卡失败：{message}")
            return 1

    except Exception as e:
        message = f"主程序异常：{e}"
        logging.error(message)
        notify_failure(message)
        send_email(f"打卡失败：{message}")
        return 1
    finally:
        logging.info("XJTU运动打卡程序结束")
        logging.info("=" * 50)


def cli() -> int:
    parser = argparse.ArgumentParser(description="XJTU 运动打卡工具")
    checks = parser.add_mutually_exclusive_group()
    checks.add_argument("--check-config", action="store_true", help="仅检查本地配置，不联网或打卡")
    checks.add_argument("--check-login", action="store_true", help="仅验证登录，不进行签到或签退")
    args = parser.parse_args()
    try:
        load_config()
    except (ValueError, OSError) as exc:
        print(f"配置检查失败：{exc}")
        return 2
    if args.check_config:
        print("配置检查通过：账号密码已设置，坐标有效，依赖已加载。")
        print(f"邮件通知：{'开启' if Config.SEND_EMAIL else '关闭'}；桌面通知：{'开启' if Config.SEND_DESKTOP_NOTIFICATION else '关闭'}")
        return 0
    setup_logging()
    if args.check_login:
        if get_token(Config.USER, Config.PASSWORD):
            print("登录验证通过，未执行签到或签退。")
            return 0
        print("登录验证失败，请查看日志。")
        return 1
    return main()


if __name__ == "__main__":
    raise SystemExit(cli())
