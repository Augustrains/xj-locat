#!/usr/bin/env python3
"""将服务模板渲染到项目目录，不访问 systemd 或安装服务。"""
import grp
import os
from pathlib import Path
import pwd


def quote_path(path):
    return str(path).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")


def main():
    root = Path(__file__).resolve().parent.parent
    python = root / ".venv/bin/python"
    if not python.is_file():
        raise SystemExit("请先创建项目 .venv 虚拟环境")
    # sudo 执行时仍以原用户身份运行服务。
    account = pwd.getpwnam(os.environ["SUDO_USER"]) if os.environ.get("SUDO_USER") else pwd.getpwuid(os.getuid())
    replacements = {
        "@PROJECT_DIR@": quote_path(root),
        "@PYTHON@": quote_path(python),
        "@RUN_USER@": account.pw_name,
        "@RUN_GROUP@": grp.getgrgid(account.pw_gid).gr_name,
    }
    for relative in ("sport-bot.service", "sport-bot.timer", "user/sport-bot.service", "user/sport-bot.timer"):
        content = (root / "systemd" / relative).read_text()
        for key, value in replacements.items():
            content = content.replace(key, value)
        target = root / ".generated/systemd" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    print("已在 .generated/systemd/ 生成当前机器的服务文件")


if __name__ == "__main__":
    main()
