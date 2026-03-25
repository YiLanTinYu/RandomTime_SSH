# SSH 交换机随机自动化连接工具

面向网络工程师的命令行运维工具。通过 Paramiko 库实现 SSH 协议连接，以**随机间隔**周期性地向网络交换机下发配置命令，并内置**奇偶轮交替执行**和**应急回滚**机制。整套工具支持完全离线部署，适用于网络隔离的生产机房环境。

---

## 主要功能

### 1. 双模式参数配置

- **交互向导模式**：无参数启动时自动进入，逐步引导输入所有参数，提交前汇总确认
- **命令行参数模式**：通过 `-H/-u/-p/-c` 等参数直接启动，适合脚本集成
- 所有输入均包含格式校验（IPv4/IPv6、端口范围、文件存在性），错误时给出明确提示

### 2. 奇偶轮交替执行

程序按轮次运行，**奇数轮执行命令文件，偶数轮执行取消命令文件**，天然实现"操作 → 撤销 → 操作 → 撤销"的交替节奏：

| 轮次 | 执行文件 | 典型用途 |
|------|---------|---------|
| 第 1、3、5…轮（奇数） | `commands.txt` | 下发配置（如添加黑洞路由） |
| 第 2、4、6…轮（偶数） | `cancel_commands.txt` | 撤销配置（如删除黑洞路由） |

> 若未配置取消文件，偶数轮跳过执行并打印警告。

### 3. 随机间隔循环

每轮完成后随机等待 **120～900 秒（2～15 分钟）**，模拟分散的人工操作节奏，每秒检测停止信号，可随时中断。

### 4. invoke_shell 交互式会话

使用 `paramiko.invoke_shell()` 替代 `exec_command()`，在单一持久 Channel 中顺序执行命令，维持华为/H3C 交换机的视图状态（用户视图 → 系统视图），避免 `sys` 后再执行其他命令时报 `SSH session not active`。

### 5. 应急停止与回滚

按 **Ctrl+C** 或发送 SIGTERM，程序立即停止主循环。若当前 SSH 连接存在且已配置取消命令文件，优先在设备上执行回滚命令再断开连接。

### 6. 双重日志输出

运行状态实时打印到终端（带颜色区分 INFO/WARN/ERROR），同时写入 `ssh_connector.log`，格式：`时间 - 级别 - 消息`。

### 7. 完全离线部署

`python_dependencies/` 目录预置所有 `.whl` 离线包；`kylin/` 目录提供针对银河麒麟 V10 的完整安装脚本体系，支持在无网络环境中完成 Python 源码编译 + 依赖安装全流程。

---

## 目录结构

```
random_start/
├── random_ssh_switch_connector.py   # 主程序（唯一核心文件）
├── requirements.txt                 # Python 依赖声明
│
├── verify_install.sh                # 通用安装验证脚本
├── get-pip.py                       # pip 离线安装器
│
├── python_dependencies/             # 离线 Python .whl 包（Windows/通用版）
│   ├── paramiko-4.0.0-py3-none-any.whl
│   ├── cryptography-46.0.5-cp311-abi3-win_amd64.whl
│   ├── bcrypt-5.0.0-cp39-abi3-win_amd64.whl
│   ├── pynacl-1.6.2-cp38-abi3-win_amd64.whl
│   ├── cffi-2.0.0-cp311-cp311-win_amd64.whl
│   ├── invoke-2.2.1-py3-none-any.whl
│   ├── pyinstaller-6.19.0-py3-none-win_amd64.whl
│   └── ...（共 16 个依赖包）
│
├── system_dependencies/             # 系统级 .deb 包目录（通用，按需放置）
│
└── kylin/                           # 银河麒麟 V10 专用部署套件
    ├── build_and_deploy.sh          # 【推荐】一键编译部署脚本（多发行版适配）
    ├── install_all_kylin.sh         # 麒麟 V10 四步安装总入口
    ├── install_system_deps_kylin.sh # 步骤 1：离线安装编译依赖
    ├── install_python_kylin.sh      # 步骤 2：源码编译 Python 3.13.12
    ├── install_project_deps_kylin.sh# 步骤 3：离线安装 Python 依赖
    ├── setup_launcher_kylin.sh      # 步骤 4：创建 run.sh 快捷入口
    ├── verify_install_kylin.sh      # 麒麟环境完整验证脚本
    ├── Python-3.13.12.tgz           # Python 源码包
    ├── get-pip.py                   # pip 离线安装器
    ├── python_dependencies/         # Linux x86_64 平台 .whl 包（10 个）
    ├── system_dependencies/         # 编译依赖 .deb 包（18 个）
    └── download_deps.py             # 在线环境下载依赖的辅助脚本
```

---

## 快速开始

### 方式一：有网络环境（通用）

```bash
# 安装依赖
pip install paramiko

# 交互向导模式（推荐初次使用）
python3 random_ssh_switch_connector.py

# 或命令行参数模式
python3 random_ssh_switch_connector.py \
    -H 192.168.1.1 -u admin -p Admin@123 \
    -c commands.txt -C cancel_commands.txt

# 密码从环境变量读取（更安全，避免密码出现在进程列表）
export SSH_PASSWORD=Admin@123
python3 random_ssh_switch_connector.py -H 192.168.1.1 -u admin -c commands.txt
```

### 方式二：离线环境安装（生产机房 / 银河麒麟 V10）

**推荐使用 `build_and_deploy.sh`（自动检测发行版，多平台适配）：**

```bash
# 赋予执行权限
chmod +x kylin/build_and_deploy.sh

# 标准安装（自动完成：编译依赖 → Python 3.13 → 项目依赖 → 启动器）
sudo bash kylin/build_and_deploy.sh

# 常用选项
sudo bash kylin/build_and_deploy.sh --skip-python   # Python 已装时跳过编译
sudo bash kylin/build_and_deploy.sh --force          # 强制重新编译 Python
sudo bash kylin/build_and_deploy.sh --help           # 查看全部选项
```

安装完成后验证并运行：

```bash
# 验证安装结果
bash kylin/verify_install_kylin.sh

# 运行程序（交互向导模式）
./run.sh

# 运行程序（命令行参数模式）
./run.sh -H 192.168.1.1 -u admin -p Admin@123 -c commands.txt

# 若创建了全局命令（root 安装时自动创建）
ssh-switch -H 192.168.1.1 -u admin -c commands.txt
```

> `build_and_deploy.sh` 适用系统：银河麒麟 V10 SP1 (x86_64 / aarch64)、Ubuntu 18.04～22.04、Debian 10～12、CentOS 7/8/Stream、RHEL 8/9、Rocky Linux 8/9。

---

## 命令行参数说明

```
python3 random_ssh_switch_connector.py [选项]
```

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `-H`, `--host` | 交换机 IP（支持 IPv4/IPv6） | 必填 |
| `-u`, `--username` | SSH 用户名 | 必填 |
| `-p`, `--password` | SSH 密码（或用环境变量 `SSH_PASSWORD`） | 必填 |
| `-P`, `--port` | SSH 端口号 | `22` |
| `-c`, `--commands-file` | 命令文件路径（奇数轮执行） | 必填 |
| `-C`, `--cancel-commands-file` | 取消命令文件路径（偶数轮执行，可选） | 无 |
| `--min-wait` | 每轮最小等待时间（秒） | `120` |
| `--max-wait` | 每轮最大等待时间（秒） | `900` |

---

## 使用步骤

### 第一步：准备命令文件

创建 `.txt` 文件，每行一条交换机命令，`#` 开头的行为注释：

**`commands.txt`（奇数轮执行）：**
```
# 进入系统视图
sys
# 添加黑洞 MAC 地址
mac-address blackhole 0440-1234-4321
```

**`cancel_commands.txt`（偶数轮执行）：**
```
# 进入系统视图
sys
# 撤销黑洞 MAC 地址
undo mac-address blackhole 0440-1234-4321
```

### 第二步：启动程序

```bash
# 交互向导（无参数启动，逐步引导输入）
python3 random_ssh_switch_connector.py

# 命令行参数（含奇偶轮交替）
python3 random_ssh_switch_connector.py \
    -H 192.168.1.1 -P 10022 \
    -u admin -p Admin@123 \
    -c commands.txt \
    -C cancel_commands.txt \
    --min-wait 120 --max-wait 300
```

### 第三步：停止程序

按 **Ctrl+C**。若配置了取消命令文件，程序在断开前自动在交换机上执行一次回滚命令。

---

## 程序运行流程

```
启动
 │
 ├─ 有命令行参数 → parse_args() 解析并校验
 └─ 无参数       → interactive_wizard() 交互向导
       │
       ▼
  参数校验（IP / 端口 / 文件存在性）
       │
       ▼
  SSHSwitchConnector.run()
       │
       ▼
┌──────────────────────────────────────────┐
│  round_num = 0                           │
│  while running:                          │
│    round_num += 1                        │
│                                          │
│    1. connect_ssh()                      │
│       paramiko 连接（超时 10s）          │
│       → 失败：等待 30s 后重试            │
│                                          │
│    2. 奇偶轮判断                         │
│       奇数轮 → execute_commands(         │
│                  commands_file)          │
│       偶数轮 → execute_commands(         │
│                  cancel_file)            │
│       （invoke_shell 交互式会话）        │
│                                          │
│    3. disconnect()                       │
│                                          │
│    4. 随机等待 random(min_wait, max_wait)│
│       每秒检测 stop_event                │
└──────────────────────────────────────────┘
       │ 按 Ctrl+C / SIGTERM
       ▼
  emergency_stop()
  执行取消命令（如已配置）→ 断开连接
```

---

## 配置参数调整

如需修改默认等待时间，有两种方式：

**命令行参数（推荐）：**
```bash
python3 random_ssh_switch_connector.py ... --min-wait 60 --max-wait 300
```

**修改源码常量：** 编辑 `random_ssh_switch_connector.py` 开头的常量块：
```python
DEFAULT_MIN_WAIT = 120   # 最小等待时间（秒）
DEFAULT_MAX_WAIT = 900   # 最大等待时间（秒）
CONNECT_TIMEOUT  = 10    # SSH 连接超时（秒）
CMD_TIMEOUT      = 30    # 单条命令超时（秒）
RETRY_WAIT       = 30    # 出错后重试等待（秒）
SHELL_CMD_DELAY  = 1.5   # 命令间延迟（秒），给交换机处理时间
```

---

## 打包为可执行文件

```bash
# 安装 pyinstaller（已含于离线包中）
pip install pyinstaller

# 打包为单文件可执行程序
pyinstaller --onefile random_ssh_switch_connector.py

# 生成的可执行文件在 dist/ 目录下
./dist/random_ssh_switch_connector --help
```

---

## 注意事项

**安全：**
- 建议通过环境变量 `SSH_PASSWORD` 传入密码，避免密码出现在进程列表和 shell 历史。
- 程序使用 `AutoAddPolicy`，自动信任所有未知主机密钥，适合内网受控环境，**不建议在公网使用**。
- `ssh_connector.log` 记录所有命令及输出，注意保护日志文件访问权限。

**命令文件：**
- 文件编码须为 **UTF-8**；空行和 `#` 注释行自动忽略；每行自动去除首尾空白。
- 华为/H3C 交换机的 `sys` 命令会进入系统视图，后续命令在同一 Shell 会话中执行，无需重复 `sys`。

**离线包平台说明：**
- `python_dependencies/`（根目录）：`win_amd64` 平台，用于 Windows 开发环境。
- `kylin/python_dependencies/`：Linux `manylinux` 平台，用于生产机房部署。
- 两者不可混用，请根据目标环境选择正确目录。

---

## 依赖清单

### Python 依赖（`requirements.txt`）

```
paramiko
pyinstaller
```

### 完整离线包（`python_dependencies/`）

| 包名 | 版本 | 用途 |
|------|------|------|
| paramiko | 4.0.0 | SSH 连接核心库 |
| cryptography | 46.0.5 | 加密算法支持 |
| bcrypt | 5.0.0 | 密码哈希 |
| pynacl | 1.6.2 | NaCl 加密 |
| cffi | 2.0.0 | C 外部函数接口 |
| pycparser | 3.0 | C 代码解析（cffi 依赖） |
| invoke | 2.2.1 | paramiko 4.x 任务依赖 |
| pyinstaller | 6.19.0 | 打包为可执行文件 |
| pyinstaller-hooks-contrib | 2026.3 | PyInstaller 钩子集合 |
| altgraph | 0.17.5 | 依赖图分析 |
| packaging | 26.0 | 版本号解析 |
| pefile | 2024.8.26 | PE 文件解析（Windows 打包） |
| pywin32-ctypes | 0.2.3 | Windows API 调用 |
| pip | 26.0.1 | pip 包管理器 |
| setuptools | 82.0.1 | 构建工具 |
| wheel | 0.46.3 | wheel 格式支持 |

### 技术栈

| 层次 | 技术/库 | 版本 | 用途 |
|------|---------|------|------|
| 命令行框架 | argparse | 内置 | 参数解析 |
| SSH 连接 | Paramiko | 4.0.0 | SSH 协议实现 |
| 加密支持 | cryptography | 46.0.5 | 加密算法 |
| 密码哈希 | bcrypt | 5.0.0 | 密码安全 |
| 加密扩展 | PyNaCl | 1.6.2 | NaCl 加密 |
| 任务依赖 | invoke | 2.2.1 | paramiko 4.x 依赖 |
| 打包工具 | PyInstaller | 6.19.0 | 生成可执行文件 |
| 标准库 | threading / signal / random / logging / re | 内置 | 线程/信号/随机/日志/正则 |
