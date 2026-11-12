# XJTU西安交大研究生体育自动打卡脚本

## 简介

此脚本用于西安交通大学研究生体美劳中体育打卡系统的自动签到与签退。脚本模拟用户登录，获取认证令牌，并在指定时间间隔后完成签到和签退操作，支持邮件通知功能。

## GitHub Actions 每日运行

### 当前部署状态（2026-11-12）

代码已部署到 `Augustrains/xj-locat`。在 GitHub 托管 Ubuntu 运行环境中，10 项离线测试及配置检查通过；OAuth 接口返回 HTTP 302，CAS 公钥接口和体育前端均返回 HTTP 200。两个必填 Repository secrets 已被成功读取。

真实登录在 MFA 检测阶段收到 `need=True`，学校要求额外安全验证，因此未执行签到、等待或签退。运行记录见 [云端验证结果](https://github.com/Augustrains/xj-locat/actions/runs/43137583320)。

首次自动登录在额外验证处停止后，已补充学校原生手机/邮箱验证码流程和同一 GitHub job 内的加密回复通道。该流程需要本人在线参与；真实 MFA 和后续独立登录的结果仍待验证。定时配置保留为北京时间 12:07，只有实际验证通过后才视为可无人值守运行。

仓库包含两个工作流：

- **XJTU Daily Sport**：每天北京时间 **12:07** 执行签到、等待 31 分钟、签退。UTC 定时表达式为 `7 4 * * *`，任务超时为 55 分钟。同一时间只运行一个打卡任务，新任务不会取消正在等待签退的任务。
- **Project Checks**：推送到 `main` 后自动运行离线测试、配置检查和不带账号的学校接口连通性探测；检查通过后，使用仓库 Secrets 验证学校登录。不会执行签到或签退。拉取请求只运行离线检查，不读取账号密码。
- **MFA Encrypted Response**：只接收临时公钥加密的验证码回复，生成回复附件供原登录任务读取。不得在其输入框中填写明文验证码。
- **XJTU Manual Login**：只有手动触发，没有定时事件，执行模式固定为登录检查。用于每日打卡启用前的人工 MFA 验证和独立登录复验。

### 必填配置

打开仓库 **Settings → Secrets and variables → Actions → Secrets → New repository secret**，逐项添加：

| Secret 名称 | 内容 |
| --- | --- |
| `SPORT_USER` | 学号 |
| `SPORT_PASSWORD` | 统一身份认证密码 |
| `SPORT_CLIENT_ID` | 本脚本客户端的固定随机标识，建议用 `python -c "import secrets; print(secrets.token_hex(16))"` 生成，勿频繁更换 |

使用 **Repository secrets**，不需要创建 Environment。工作流直接读取环境变量，不需要上传 `.env`。代码、依赖、配置示例和测试可以上传；`.env`、`.venv`、日志和 `.generated` 不应提交。

### 首次验证和手动运行

1. 在 **Actions → Project Checks** 查看首次推送的检查结果，确认 `tests` 和 `cloud-login` 均通过。
2. 打开 **Actions → XJTU Daily Sport → Run workflow**，选择 `main` 分支。
3. `mode=login` 只检查登录；`mode=run` 执行一次完整签到、等待和签退，通常需要 32～36 分钟。手动默认选项为 `login`。
4. 确认一次 `run` 成功后，继续观察每日 12:07 的定时结果。学校确认当天已有积分时，脚本正常退出并跳过等待。

### 学校要求短信或安全邮箱验证码时

1. 本人在线时，手动运行 **XJTU Manual Login**，勾选 `request_mfa`，按需选择 `securephone` 或 `secureemail`。它没有定时事件，不会执行签到或签退。无人值守的每日运行不会自动发送验证码。
2. 学校确认发送验证码后，任务发布 `mfa-challenge-运行ID-尝试次数` 附件并等待最多 10 分钟。下载其中的 `challenge.json`。
3. 在本地项目目录运行 `python scripts/encrypt_mfa_reply.py challenge.json .generated/mfa-reply.json`，按提示输入验证码。如本人同意学校的“设为可信客户端”选项，增加 `--trust-client`；默认不勾选。
4. 将加密回复交给专用工作流：`gh workflow run mfa-response.yml --repo Augustrains/xj-locat --ref main --json < .generated/mfa-reply.json`（PowerShell 使用 `Get-Content -Raw .generated/mfa-reply.json | gh workflow run mfa-response.yml --repo Augustrains/xj-locat --ref main --json`）。
5. 原任务在同一机器、同一登录会话中校验验证码并继续。过期或校验失败时停止，不自动反复发送或猜测验证码。

回复附件使用 RSA-OAEP SHA-256 加密，临时私钥和登录 Cookie 只留在原 job；公开附件没有明文验证码、私钥或登录会话。一次性回复只能用于对应运行和对应挑战。

MFA 登录通过后，再启动一次 `request_mfa=false` 的独立登录检查。只有该检查也成功，才有依据判断下一次定时登录可以自动完成；学校仍可能根据新的网络来源要求验证。

工作流默认关闭桌面通知和邮件。Actions 页面提供步骤日志与运行摘要，不上传额外日志附件。日志会过滤配置中的账号密码、邮件授权码和认证 URL 参数。

### 可选配置

在同一页面的 **Variables** 下添加仓库变量：

| Variable 名称 | 默认值 / 用途 |
| --- | --- |
| `SPORT_LONGITUDE` | `108.654387` |
| `SPORT_LATITUDE` | `34.257229` |
| `SPORT_SEND_EMAIL` | `false`；设为 `true` 启用邮件 |

启用邮件时，还需添加三个 Repository secrets：`SPORT_SMTP_AUTH_CODE`、`SPORT_EMAIL_SENDER`、`SPORT_EMAIL_RECEIVER`。脚本使用 QQ 邮箱 SMTP SSL 465 端口。

### 运行限制

- GitHub 定时任务可能延迟或漏执行，不保证准时到秒；工作流必须在默认分支 `main`。公开仓库连续 60 天无仓库活动，定时任务会自动停用，需要在 Actions 页面重新启用。
- 云端网络连通、学校登录成功、完整打卡成功是三个不同的验证结果。学校接口可能限制网络来源，CAS 也可能要求额外验证；无人值守任务遇到额外验证会停止，手动任务可由本人完成安全手机或安全邮箱验证。
- 每次登录创建新的会话，不保存可信设备 Cookie。浏览器手动登录成功不代表 GitHub 运行环境可以免验证。
- 签到成功后如果任务被取消或签退失败，需要检查学校系统；脚本没有跨任务恢复未完成签退的功能，不要在 31 分钟等待中取消任务。
- 标准公开仓库运行环境免费；私有仓库会计入账号 Actions 额度，31 分钟等待也计算执行时长。
- 暂停每日运行：Actions → XJTU Daily Sport → 右上角菜单 → Disable workflow。恢复时选择 Enable workflow。

参考：[定时事件说明](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)、[Secrets 配置](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets)。

---

## 版本说明

<details>
<summary><strong>v3.0 (2025-10-12)</strong></summary>

- 增加mfa认证过程，修复了获取访问令牌失败的问题（感谢[@takagi18065](https://github.com/takagi18065)
）。
</details>

<details>
<summary><strong>v2.0 (2025-07-20)</strong></summary>

- **接口更新**：修复了由于学校体育打卡系统接口变更导致的登录失败问题。
</details>

<details>
<summary><strong>v1.0 (初始版本)</strong></summary>

- 基础的CAS登录认证功能
- 自动签到和签退操作
- 邮件通知功能
- 基本的日志记录

</details>

---

## 环境配置

程序从项目目录中的 `.env` 读取配置，环境变量优先。`.env` 已加入 `.gitignore`，需要自行创建并填写，请勿分享此文件。

本地配置完成后检查配置：

```bash
.venv/bin/python sport_bot.py --check-config
```

在其他机器上安装时：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
```

编辑 `.env`，填写 `SPORT_USER`（学号）和 `SPORT_PASSWORD`（统一认证密码）。账号和密码不再写入 `sport_bot.py`。配置值可使用双引号；双引号内的反斜线需要写成 `\\`，双引号需要写成 `\"`，也可以用单引号包裹不含单引号的密码。

坐标通过 `SPORT_LONGITUDE`、`SPORT_LATITUDE` 配置，当前保留原有涵英楼北草坪坐标。签到后等待 31 分钟再签退。

## 通知配置

默认关闭邮件，开启失败时的桌面通知。启用邮件时，在 `.env` 中填写：

```dotenv
SPORT_SEND_EMAIL="true"
SPORT_SMTP_AUTH_CODE="QQ邮箱SMTP授权码"
SPORT_EMAIL_SENDER="发件邮箱@qq.com"
SPORT_EMAIL_RECEIVER="接收邮箱@example.com"
```

SMTP 授权码需要从 QQ 邮箱设置中获取。配置缺失时，程序会在联网前报错。桌面通知需要 `notify-send` 和可访问的桌面会话。

## 验证与运行

仅检查本地配置，不联网：

```bash
.venv/bin/python sport_bot.py --check-config
```

仅验证学校登录，不签到或签退：

```bash
.venv/bin/python sport_bot.py --check-login
```

正常执行一次签到、等待、签退：

```bash
.venv/bin/python sport_bot.py
```

如果学校接口返回“你今天已经获得了锻炼积分，请明天继续”，程序会按今日已完成正常退出，不再等待或执行签退，也不会发送失败通知。其他签到或签退错误仍会报告失败。

脚本直连学校接口，不使用系统代理。若出现 DNS 或网络错误，请检查学校域名解析和网络连接；若 CAS 要求额外认证，请根据日志提示在浏览器完成验证。

## 每日定时运行（Linux / systemd）

推荐用户级服务。在项目目录执行：

```bash
bash systemd/install-user.sh
```

安装脚本会先检查配置，按当前项目路径生成服务文件，再安装并启用定时器，无需修改旧电脑路径。定时器每天北京时间 12:00 触发，随机延迟最多 2 分钟；电脑需开机且用户服务可运行。保留 `Persistent=true`，恢复运行时会补执行错过的任务。

查看运行状态和日志：

```bash
systemctl --user list-timers sport-bot.timer
systemctl --user status sport-bot.service
journalctl --user -u sport-bot.service -n 50 --no-pager
```

停止定时任务：

```bash
systemctl --user disable --now sport-bot.timer
```

用户退出登录后仍需运行的机器，可选择系统级服务 `bash systemd/install.sh`（需要 sudo）。用户级和系统级服务只启用一种。修改 `.env` 后下次执行自动生效；修改定时器模板后重新运行相应安装脚本。

只生成当前机器的服务文件，不安装或启动：

```bash
bash systemd/install-user.sh --prepare-only
```

生成文件位于 `.generated/systemd/`。脚本日志位于项目目录的 `sport_bot.log`。

## 回归测试

可用 `--check-login` 验证登录，用 `systemctl --user list-timers sport-bot.timer` 检查 Linux 定时任务是否启用，用学校系统和日志确认实际打卡结果。本地没有成功日志不代表学校系统中当天未完成。

运行回归测试（使用模拟请求，不联网或打卡）：

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## 免责声明

此脚本仅供学习和交流使用，请遵守学校相关规定。
