#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SSH 交换机随机自动化连接工具 (命令行版)
用法:
  交互模式:  python random_ssh_switch_connector.py
  参数模式:  python random_ssh_switch_connector.py -H <IP> -u <用户名> -p <密码>
             -c <命令文件> [-P <端口>] [-C <取消命令文件>]
             [--min-wait <秒>] [--max-wait <秒>]
  帮助:      python random_ssh_switch_connector.py --help
"""

import argparse
import getpass
import logging
import os
import re
import random
import signal
import sys
import time
import threading

import paramiko

# ───────────────────────── 日志配置 ─────────────────────────
LOG_FILE = "ssh_connector.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# ───────────────────────── 常量 ─────────────────────────
DEFAULT_PORT     = 22
DEFAULT_MIN_WAIT = 120   # 2 分钟
DEFAULT_MAX_WAIT = 900   # 15 分钟
CONNECT_TIMEOUT  = 10    # SSH 连接超时（秒）
CMD_TIMEOUT      = 30    # 单条命令超时（秒）
RETRY_WAIT       = 30    # 错误后重试等待（秒）——交换机 VTY 资源有限，等长一些避免占满

# invoke_shell 参数
SHELL_RECV_BYTES  = 65535   # 每次读取缓冲区大小
SHELL_CMD_DELAY   = 1.5     # 每条命令发送后等待时间（秒），给交换机处理时间
SHELL_READY_WAIT  = 2.0     # 建立 shell 后等待就绪提示（秒）

# ───────────────────────── 颜色输出（可选）─────────────────────────
def _supports_color():
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

def _color(text, code):
    return f"\033[{code}m{text}\033[0m" if _supports_color() else text

def info(msg):    print(_color(f"[INFO]  {msg}", "36"))
def ok(msg):      print(_color(f"[OK]    {msg}", "32"))
def warn(msg):    print(_color(f"[WARN]  {msg}", "33"))
def err(msg):     print(_color(f"[ERROR] {msg}", "31"), file=sys.stderr)
def banner(msg):  print(_color(f"\n{'─'*60}\n  {msg}\n{'─'*60}", "1"))


# ───────────────────────── 参数校验 ─────────────────────────
def validate_ip(ip: str) -> bool:
    """验证 IPv4 或 IPv6 地址格式"""
    ipv4 = r"^((25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(25[0-5]|2[0-4]\d|[01]?\d\d?)$"
    ipv6 = (
        r"^([0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}$"
        r"|^([0-9a-fA-F]{1,4}:){1,7}:$"
        r"|^([0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}$"
        r"|^([0-9a-fA-F]{1,4}:){1,5}(:[0-9a-fA-F]{1,4}){1,2}$"
        r"|^([0-9a-fA-F]{1,4}:){1,4}(:[0-9a-fA-F]{1,4}){1,3}$"
        r"|^([0-9a-fA-F]{1,4}:){1,3}(:[0-9a-fA-F]{1,4}){1,4}$"
        r"|^([0-9a-fA-F]{1,4}:){1,2}(:[0-9a-fA-F]{1,4}){1,5}$"
        r"|^[0-9a-fA-F]{1,4}:((:[0-9a-fA-F]{1,4}){1,6})$"
        r"|^:((:[0-9a-fA-F]{1,4}){1,7}|:)$"
        r"|^fe80:(:[0-9a-fA-F]{0,4}){0,4}%[0-9a-zA-Z]+$"
        r"|^::(ffff(:0{1,4})?:)?((25[0-5]|(2[0-4]|1?\d)?\d)\.){3}(25[0-5]|(2[0-4]|1?\d)?\d)$"
        r"|^([0-9a-fA-F]{1,4}:){1,4}:((25[0-5]|(2[0-4]|1?\d)?\d)\.){3}(25[0-5]|(2[0-4]|1?\d)?\d)$"
    )
    return bool(re.match(ipv4, ip) or re.match(ipv6, ip))


def validate_port(port_str: str) -> int:
    """验证端口号，返回整数；失败抛出 ValueError"""
    try:
        port = int(port_str)
    except (TypeError, ValueError):
        raise ValueError(f"端口号必须是整数，收到: {port_str!r}")
    if not (1 <= port <= 65535):
        raise ValueError(f"端口号必须在 1~65535 范围内，收到: {port}")
    return port


def validate_file(path: str, label: str = "文件") -> str:
    """验证文件路径存在，返回绝对路径；失败抛出 ValueError"""
    if not path:
        raise ValueError(f"{label}路径不能为空")
    abs_path = os.path.abspath(path)
    if not os.path.isfile(abs_path):
        raise ValueError(f"{label}不存在: {abs_path}")
    return abs_path


def read_commands(file_path: str) -> list:
    """读取命令文件，忽略空行和 # 注释行，返回命令列表"""
    with open(file_path, "r", encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip() and not l.strip().startswith("#")]
    return lines


# ───────────────────────── 交互式配置向导 ─────────────────────────
def _prompt(label: str, default=None, secret=False, validator=None):
    """
    通用交互输入。
    - secret=True 使用 getpass（不回显）
    - validator 是一个可调用对象，校验失败应抛出 ValueError
    返回校验通过的值。
    """
    suffix = f" [{default}]" if default is not None else ""
    prompt_text = f"  {label}{suffix}: "
    while True:
        try:
            raw = (getpass.getpass(prompt_text) if secret
                   else input(prompt_text)).strip()
        except (KeyboardInterrupt, EOFError):
            print()
            raise KeyboardInterrupt("用户取消输入")

        if not raw and default is not None:
            raw = str(default)

        if not raw:
            err(f"{label} 不能为空，请重新输入")
            continue

        if validator:
            try:
                return validator(raw)
            except ValueError as e:
                err(str(e))
        else:
            return raw


def interactive_wizard() -> dict:
    """引导用户逐步输入所有参数，返回配置字典"""
    banner("SSH 交换机随机自动化连接工具  —  参数配置向导")
    print(
        "  提示：直接回车可使用 [默认值]；密码输入时不会显示字符。\n"
        "  按 Ctrl+C 可随时退出程序。\n"
    )

    # ── 连接参数 ──
    print("【连接参数】")
    ip = _prompt(
        "交换机 IP 地址 (支持 IPv4 / IPv6)",
        validator=lambda v: v if validate_ip(v) else (_ for _ in ()).throw(
            ValueError(f"IP 格式不正确: {v!r}，请输入有效的 IPv4 或 IPv6 地址")
        ),
    )
    port = _prompt("SSH 端口号", default=DEFAULT_PORT, validator=validate_port)
    username = _prompt("SSH 用户名")
    password = _prompt("SSH 密码", secret=True,
                       validator=lambda v: v if v else (_ for _ in ()).throw(
                           ValueError("密码不能为空")))

    # ── 文件路径 ──
    print("\n【命令文件】")
    print("  命令文件格式：每行一条命令，# 开头的行视为注释，空行自动忽略。")
    print("  示例：")
    print("    show version")
    print("    show ip interface brief")
    commands_file = _prompt(
        "命令文件路径",
        validator=lambda v: validate_file(v, "命令文件"),
    )

    print("\n【取消命令文件（可选）】")
    print("  用于应急回滚，按 Enter 跳过。")
    cancel_file = None
    raw_cancel = input("  取消命令文件路径 (留空跳过): ").strip()
    if raw_cancel:
        try:
            cancel_file = validate_file(raw_cancel, "取消命令文件")
            ok(f"取消命令文件: {cancel_file}")
        except ValueError as e:
            warn(f"{e}，取消命令文件将被忽略")

    # ── 随机等待时间 ──
    print("\n【随机等待时间范围】")
    print(f"  每轮执行完毕后，随机等待 min~max 秒再重连（默认 {DEFAULT_MIN_WAIT}~{DEFAULT_MAX_WAIT} 秒）。")

    def _validate_wait(v, label):
        try:
            n = int(v)
        except ValueError:
            raise ValueError(f"{label} 必须是整数")
        if n < 1:
            raise ValueError(f"{label} 必须 ≥ 1 秒")
        return n

    min_wait = _prompt("最小等待时间（秒）", default=DEFAULT_MIN_WAIT,
                       validator=lambda v: _validate_wait(v, "最小等待时间"))
    max_wait = _prompt("最大等待时间（秒）", default=DEFAULT_MAX_WAIT,
                       validator=lambda v: _validate_wait(v, "最大等待时间"))

    if min_wait > max_wait:
        warn(f"最小值 {min_wait} > 最大值 {max_wait}，已自动交换")
        min_wait, max_wait = max_wait, min_wait

    # ── 汇总确认 ──
    print()
    banner("配置确认")
    print(f"  目标主机  : {ip}:{port}")
    print(f"  用户名    : {username}")
    print(f"  密码      : {'*' * len(password)}")
    print(f"  命令文件  : {commands_file}")
    print(f"  取消文件  : {cancel_file or '（未配置）'}")
    print(f"  等待范围  : {min_wait}~{max_wait} 秒")
    print()

    confirm = input("  确认以上参数并启动程序？[Y/n]: ").strip().lower()
    if confirm in ("n", "no"):
        print("已取消，程序退出。")
        sys.exit(0)

    return {
        "ip": ip,
        "port": port,
        "username": username,
        "password": password,
        "commands_file": commands_file,
        "cancel_commands_file": cancel_file,
        "min_wait": min_wait,
        "max_wait": max_wait,
    }


# ───────────────────────── 命令行参数解析 ─────────────────────────
def parse_args() -> dict | None:
    """
    解析命令行参数。
    若未提供任何参数，返回 None（触发交互向导）。
    """
    parser = argparse.ArgumentParser(
        prog="random_ssh_switch_connector.py",
        description="SSH 交换机随机自动化连接工具（命令行版）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 交互式向导（推荐初次使用）
  python random_ssh_switch_connector.py

  # 全参数启动
  python random_ssh_switch_connector.py \\
      -H 192.168.1.1 -u admin -p Admin@123 \\
      -c commands.txt -C cancel_commands.txt \\
      --min-wait 60 --max-wait 300

  # 密码从环境变量读取（更安全）
  SSH_PASSWORD=Admin@123 python random_ssh_switch_connector.py \\
      -H 192.168.1.1 -u admin -c commands.txt

命令文件格式:
  每行一条交换机命令，# 开头的行为注释，空行忽略。
  示例:
    # 查看设备基本信息
    show version
    show ip interface brief
        """,
    )

    conn = parser.add_argument_group("连接参数（必填）")
    conn.add_argument("-H", "--host",     metavar="IP",   help="交换机 IP 地址（支持 IPv4 / IPv6）")
    conn.add_argument("-u", "--username", metavar="USER", help="SSH 登录用户名")
    conn.add_argument("-p", "--password", metavar="PASS",
                      help="SSH 登录密码（也可通过环境变量 SSH_PASSWORD 传入，避免明文暴露）")
    conn.add_argument("-P", "--port",     metavar="PORT", type=int, default=DEFAULT_PORT,
                      help=f"SSH 端口号（默认 {DEFAULT_PORT}）")

    files = parser.add_argument_group("文件参数")
    files.add_argument("-c", "--commands-file",        metavar="FILE", help="命令文件路径（必填）")
    files.add_argument("-C", "--cancel-commands-file", metavar="FILE",
                       help="取消命令文件路径（可选，用于应急回滚）")

    timing = parser.add_argument_group("等待时间")
    timing.add_argument("--min-wait", metavar="SEC", type=int, default=DEFAULT_MIN_WAIT,
                        help=f"每轮最小等待时间，秒（默认 {DEFAULT_MIN_WAIT}）")
    timing.add_argument("--max-wait", metavar="SEC", type=int, default=DEFAULT_MAX_WAIT,
                        help=f"每轮最大等待时间，秒（默认 {DEFAULT_MAX_WAIT}）")

    # 没有任何参数 → 交互向导
    if len(sys.argv) == 1:
        return None

    args = parser.parse_args()

    # ── 必填项校验 ──
    errors = []

    if not args.host:
        errors.append("-H/--host  交换机 IP 地址（必填）")
    elif not validate_ip(args.host):
        errors.append(f"-H/--host  IP 格式不正确: {args.host!r}")

    if not args.username:
        errors.append("-u/--username  SSH 用户名（必填）")

    # 密码优先级：命令行 > 环境变量 > 交互输入
    password = args.password or os.environ.get("SSH_PASSWORD")
    if not password:
        try:
            password = getpass.getpass(f"  输入 {args.username}@{args.host} 的 SSH 密码: ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            err("未输入密码，程序退出")
            sys.exit(1)
    if not password:
        errors.append("-p/--password  SSH 密码（必填）")

    if not args.commands_file:
        errors.append("-c/--commands-file  命令文件路径（必填）")

    if errors:
        err("参数错误，请修正以下问题：")
        for e in errors:
            print(f"  ✗  {e}")
        print(f"\n运行  python {parser.prog} --help  查看完整帮助。")
        sys.exit(1)

    # ── 文件/端口校验 ──
    try:
        validate_port(str(args.port))
    except ValueError as e:
        err(str(e))
        sys.exit(1)

    try:
        commands_file = validate_file(args.commands_file, "命令文件")
    except ValueError as e:
        err(str(e))
        sys.exit(1)

    cancel_file = None
    if args.cancel_commands_file:
        try:
            cancel_file = validate_file(args.cancel_commands_file, "取消命令文件")
        except ValueError as e:
            warn(f"{e}，取消命令文件将被忽略")

    if args.min_wait < 1:
        err("--min-wait 必须 ≥ 1 秒")
        sys.exit(1)
    if args.max_wait < 1:
        err("--max-wait 必须 ≥ 1 秒")
        sys.exit(1)

    min_wait = args.min_wait
    max_wait = args.max_wait
    if min_wait > max_wait:
        warn(f"--min-wait {min_wait} > --max-wait {max_wait}，已自动交换")
        min_wait, max_wait = max_wait, min_wait

    return {
        "ip": args.host,
        "port": args.port,
        "username": args.username,
        "password": password,
        "commands_file": commands_file,
        "cancel_commands_file": cancel_file,
        "min_wait": min_wait,
        "max_wait": max_wait,
    }


# ───────────────────────── 核心业务逻辑 ─────────────────────────
class SSHSwitchConnector:
    """SSH 交换机随机自动化连接控制器（无 GUI）"""

    def __init__(self, config: dict):
        self.ip               = config["ip"]
        self.port             = config["port"]
        self.username         = config["username"]
        self.password         = config["password"]
        self.commands_file    = config["commands_file"]
        self.cancel_file      = config.get("cancel_commands_file")
        self.min_wait         = config["min_wait"]
        self.max_wait         = config["max_wait"]

        self.running          = False
        self.stop_event       = threading.Event()
        self.ssh_client       = None
        self._lock            = threading.Lock()

    # ── 日志 ──
    def log(self, msg: str):
        logger.info(msg)

    # ── SSH 操作 ──
    def connect_ssh(self):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=self.ip,
            port=self.port,
            username=self.username,
            password=self.password,
            timeout=CONNECT_TIMEOUT,
        )
        self.log(f"成功连接到 {self.ip}:{self.port}")
        return client

    def execute_commands(self, client, file_path: str, label: str = "命令"):
        """
        使用 invoke_shell 交互式 Shell 执行命令。

        原因：华为/H3C 交换机命令（如 sys、mac-address blackhole）需要维持
        会话视图状态（用户视图 → 系统视图）。exec_command() 每条命令独立开
        Channel，Channel 关闭后视图状态丢失，导致：
          - 第二条命令报 "SSH session not active"
          - 交换机主动断开连接（Connection reset by peer 104）

        invoke_shell 复用同一 Channel，所有命令在同一交互会话中顺序执行，
        视图状态在命令间得以保持。
        """
        commands = read_commands(file_path)
        if not commands:
            warn(f"{label}文件为空: {file_path}")
            return

        # 开启交互式 shell
        shell = client.invoke_shell(width=200, height=50)
        shell.settimeout(CMD_TIMEOUT)

        # 等待交换机登录提示就绪（读取欢迎横幅等初始输出）
        time.sleep(SHELL_READY_WAIT)
        if shell.recv_ready():
            banner_output = shell.recv(SHELL_RECV_BYTES).decode("utf-8", errors="ignore")
            self.log(f"Shell 就绪，初始输出: {banner_output.strip()[:200]}")

        for cmd in commands:
            if self.stop_event.is_set():
                break

            self.log(f"执行{label}: {cmd}")
            # 发送命令（华为/H3C 交换机需要 \n 换行符）
            shell.send(cmd + "\n")

            # 等待命令执行完毕并收集输出
            time.sleep(SHELL_CMD_DELAY)
            output_parts = []
            # 循环读取直到没有更多数据
            deadline = time.time() + CMD_TIMEOUT
            while time.time() < deadline:
                if shell.recv_ready():
                    chunk = shell.recv(SHELL_RECV_BYTES).decode("utf-8", errors="ignore")
                    output_parts.append(chunk)
                    # 如果输出以命令提示符结尾，认为命令已完成
                    combined = "".join(output_parts)
                    if re.search(r'[\]>]\s*$', combined):
                        break
                    time.sleep(0.3)
                else:
                    break

            output = "".join(output_parts).strip()
            if output:
                self.log(f"输出: {output[:500]}")  # 截断超长输出避免日志膨胀

        # 关闭 shell channel
        try:
            shell.close()
        except Exception:
            pass

    def disconnect(self, client=None):
        target = client or self.ssh_client
        if target:
            try:
                target.close()
                self.log("SSH 连接已断开")
            except Exception as e:
                self.log(f"断开连接时出错: {e}")
            finally:
                if target is self.ssh_client:
                    self.ssh_client = None

    # ── 应急断开 ──
    def emergency_stop(self):
        """信号处理：立即停止主循环，执行回滚命令后断开"""
        if not self.running:
            return
        info("收到停止信号，正在安全退出...")
        self.stop_event.set()
        self.running = False

        with self._lock:
            client = self.ssh_client

        if client and self.cancel_file:
            info("正在执行取消命令（回滚）...")
            try:
                self.execute_commands(client, self.cancel_file, label="取消")
            except Exception as e:
                self.log(f"执行取消命令失败: {e}")
            self.disconnect(client)
        elif client:
            self.disconnect(client)

        ok("程序已安全退出")

    # ── 主循环 ──
    def run(self):
        self.running = True
        self.stop_event.clear()

        banner(f"程序启动  →  目标: {self.ip}:{self.port}  用户: {self.username}")
        info(f"命令文件  : {self.commands_file}")
        info(f"取消文件  : {self.cancel_file or '（未配置）'}")
        info(f"等待范围  : {self.min_wait}~{self.max_wait} 秒")
        info(f"日志文件  : {os.path.abspath(LOG_FILE)}")
        info("按 Ctrl+C 可随时安全停止程序")
        print()

        round_num = 0
        while self.running and not self.stop_event.is_set():
            round_num += 1
            info(f"══ 第 {round_num} 轮开始 ══")
            client = None
            try:
                # 1. 连接
                info("正在建立 SSH 连接...")
                client = self.connect_ssh()
                with self._lock:
                    self.ssh_client = client

                if self.stop_event.is_set():
                    break

                # 2. 执行命令（奇数轮执行正向命令，偶数轮执行取消命令）
                if round_num % 2 == 1:
                    info(f"第 {round_num} 轮（奇数）：开始执行命令...")
                    self.execute_commands(client, self.commands_file)
                elif self.cancel_file:
                    info(f"第 {round_num} 轮（偶数）：开始执行取消命令...")
                    self.execute_commands(client, self.cancel_file, label="取消")
                else:
                    warn(f"第 {round_num} 轮（偶数）：未配置取消命令文件，跳过本轮执行")

                if self.stop_event.is_set():
                    break

                # 3. 断开
                self.disconnect(client)
                with self._lock:
                    self.ssh_client = None
                client = None

                # 4. 随机等待
                wait_time = random.randint(self.min_wait, self.max_wait)
                info(f"本轮完成，等待 {wait_time} 秒后重连"
                     f"（预计 {time.strftime('%H:%M:%S', time.localtime(time.time() + wait_time))} 开始下一轮）")

                for _ in range(wait_time):
                    if self.stop_event.is_set():
                        break
                    time.sleep(1)

            except Exception as e:
                self.log(f"第 {round_num} 轮出现异常: {e}")
                warn(f"等待 {RETRY_WAIT} 秒后重试...")
                if client:
                    self.disconnect(client)
                    with self._lock:
                        self.ssh_client = None
                for _ in range(RETRY_WAIT):
                    if self.stop_event.is_set():
                        break
                    time.sleep(1)

        # 确保连接已关闭
        with self._lock:
            if self.ssh_client:
                self.disconnect()

        ok("主循环已结束")


# ───────────────────────── 入口 ─────────────────────────
def main():
    # 解析参数（无参数时触发交互向导）
    config = parse_args()
    if config is None:
        try:
            config = interactive_wizard()
        except KeyboardInterrupt:
            print("\n用户取消，程序退出。")
            sys.exit(0)

    connector = SSHSwitchConnector(config)

    # 注册信号处理（Ctrl+C / kill）
    def _signal_handler(sig, frame):
        print()
        connector.emergency_stop()
        sys.exit(0)

    signal.signal(signal.SIGINT,  _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # 启动主循环
    connector.run()


if __name__ == "__main__":
    main()
