"""本地加密用户提供的验证码；输出文件可交给 gh workflow run --json。"""

import argparse
import getpass
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mfa_auth import encrypt_reply


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("challenge_file", type=Path)
    parser.add_argument("output_file", type=Path)
    parser.add_argument("--trust-client", action="store_true", help="已明确同意学校的可信客户端选项")
    args = parser.parse_args()
    challenge = json.loads(args.challenge_file.read_text(encoding="utf-8"))
    code = getpass.getpass("学校验证码（不回显）：").strip()
    encrypted = encrypt_reply(challenge["public_key"], challenge["run_id"], challenge["challenge_id"], code, args.trust_client)
    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    args.output_file.write_text(json.dumps({"target_run_id": challenge["run_id"],
                                           "challenge_id": challenge["challenge_id"],
                                           "encrypted_code": encrypted}), encoding="utf-8")
    print("加密回复已生成，文件不含明文验证码。")


if __name__ == "__main__":
    main()
