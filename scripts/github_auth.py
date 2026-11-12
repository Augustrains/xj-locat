"""同一 GitHub job 内准备/恢复学校登录；人工验证码只通过加密附件传递。"""

import argparse
import io
import json
import logging
import os
from pathlib import Path
import sys
import time
import uuid
import zipfile

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sport_bot as bot
from mfa_auth import MfaError, decrypt_reply, new_reply_key, start_challenge, validate_challenge

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / ".generated/mfa/private-session.json"
PUBLIC = ROOT / ".generated/mfa/public/challenge.json"


def save_context(context, challenge, private_key, run_id, challenge_id, mode):
    body = {key: value for key, value in context.items() if key != "session"}
    body.update({
        "cookies": [{"name": c.name, "value": c.value, "domain": c.domain,
                     "path": c.path, "secure": c.secure, "expires": c.expires,
                     "rest": c._rest} for c in context["session"].cookies],
        "challenge": challenge, "private_key": private_key,
        "run_id": run_id, "challenge_id": challenge_id, "mode": mode,
        "deadline": time.time() + 600,
    })
    PRIVATE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(PRIVATE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(body, stream)


def load_context():
    body = json.loads(PRIVATE.read_text(encoding="utf-8"))
    session = requests.Session()
    session.trust_env = False
    session.headers["User-Agent"] = bot.Config.UA
    for cookie in body.pop("cookies"):
        session.cookies.set_cookie(requests.cookies.create_cookie(**cookie))
    body["session"] = session
    return body


def execute(context, mode):
    token = bot.finish_login(context)
    logging.info("学校登录验证通过。")
    return bot.main(token=token) if mode == "run" else 0


def prepare(mode, request_mfa, channel):
    context = bot.prepare_login(bot.Config.USER, bot.Config.PASSWORD)
    try:
        if not context["mfa_required"]:
            return execute(context, mode)
        if not request_mfa:
            raise MfaError("需要本人二次验证。手动运行并启用 request_mfa 后才会发送验证码。")
        run_id = os.environ.get("GITHUB_RUN_ID", "")
        if not run_id.isdigit():
            raise MfaError("人工验证码流程必须在 GitHub Actions 中启动。")
        private_key, public_key = new_reply_key()
        challenge_id = uuid.uuid4().hex
        challenge = start_challenge(context["session"], context["form_data"].get("mfaState", ""), channel)
        save_context(context, challenge, private_key, run_id, challenge_id, mode)
        PUBLIC.parent.mkdir(parents=True, exist_ok=True)
        PUBLIC.write_text(json.dumps({"run_id": run_id, "challenge_id": challenge_id,
                                      "public_key": public_key, "channel": channel}), encoding="utf-8")
        output = os.environ.get("GITHUB_OUTPUT")
        if not output:
            raise MfaError("缺少 GitHub 步骤输出配置。")
        with open(output, "a", encoding="utf-8") as stream:
            stream.write("mfa_required=true\n")
        logging.info("验证码已发送至学校绑定的%s；将在本次任务中等待加密回复，最多 10 分钟。",
                     "安全手机" if channel == "securephone" else "安全邮箱")
        return 0
    finally:
        context["session"].close()


def wait_for_reply(context):
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GH_MFA_ARTIFACT_TOKEN", "")
    if not repo or not token:
        raise MfaError("缺少读取加密回复附件的 GitHub 配置。")
    artifact_name = f"mfa-response-{context['run_id']}-{context['challenge_id']}"
    base = f"https://api.github.com/repos/{repo}"
    visited = set()
    with requests.Session() as api:
        api.trust_env = False
        api.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
        while time.time() < context["deadline"]:
            response = api.get(f"{base}/actions/artifacts", params={"name": artifact_name, "per_page": 10}, timeout=30)
            response.raise_for_status()
            for artifact in response.json().get("artifacts", []):
                if artifact["id"] in visited or artifact.get("expired"):
                    continue
                source = api.get(f"{base}/actions/runs/{artifact['workflow_run']['id']}", timeout=30)
                source.raise_for_status()
                run = source.json()
                if run.get("conclusion") is None:
                    continue
                visited.add(artifact["id"])
                if (run.get("conclusion") != "success" or run.get("event") != "workflow_dispatch"
                        or run.get("path") != ".github/workflows/mfa-response.yml" or run.get("head_branch") != "main"):
                    continue
                download = api.get(f"{base}/actions/artifacts/{artifact['id']}/zip", timeout=30)
                download.raise_for_status()
                if len(download.content) > 16384:
                    raise MfaError("验证码附件大小异常。")
                with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
                    entry = archive.getinfo("response.json")
                    if entry.file_size > 4096:
                        raise MfaError("验证码回复大小异常。")
                    encrypted = json.loads(archive.read(entry))["encrypted_code"]
                return decrypt_reply(context["private_key"], encrypted, context["run_id"], context["challenge_id"])
            time.sleep(min(15, max(0, context["deadline"] - time.time())))
    raise MfaError("等待验证码已超时；如需继续请重新启动手动验证，不复用旧验证码。")


def resume():
    context = load_context()
    try:
        reply = wait_for_reply(context)
        validate_challenge(context["session"], context["challenge"], reply["code"])
        context["form_data"]["trustAgent"] = "true" if reply["trust_client"] else "false"
        logging.info("学校二次验证通过，继续原登录会话。")
        return execute(context, context["mode"])
    finally:
        context["session"].close()
        PRIVATE.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("prepare", "resume"))
    parser.add_argument("--mode", choices=("login", "run"), default="login")
    parser.add_argument("--request-mfa", choices=("true", "false"), default="false")
    parser.add_argument("--channel", choices=("securephone", "secureemail"), default="securephone")
    args = parser.parse_args()
    bot.load_config()
    bot.setup_logging()
    try:
        if args.phase == "prepare":
            return prepare(args.mode, args.request_mfa == "true", args.channel)
        return resume()
    except Exception as error:
        logging.error("登录流程停止（%s）：%s", type(error).__name__, error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
