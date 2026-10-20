# Hummingbot 纸面撮合部署

配置依据官方 [Client Quickstart](https://hummingbot.org/installation/hummingbot-client/) 与 [Paper Trade](https://hummingbot.org/client/global-configs/paper-trade/)。版本固定为 2.17.0，镜像 digest 在 2026-10-20 从 Docker Hub 的官方 `hummingbot/hummingbot` 仓库核验。

## 先检查环境

```console
python -m deployment.preflight
```

结果保存到 `runtime/deployment_preflight.json`。必须具备可用的 Linux Docker 引擎和 Compose 插件。Windows 推荐 Docker Desktop、WSL2 和 Ubuntu；安装与首次初始化需要本机操作，当前普通权限会话不能代替 Windows 管理员授权或系统重启。

本机 2026-10-20 的检查发现 Docker 和 WSL 不在 PATH，进程不是管理员。因此容器启动与 Hummingbot 撮合验收尚未完成；原生 Python 观察器可以独立运行。

Windows 前提按 [微软 WSL 安装说明](https://learn.microsoft.com/en-us/windows/wsl/install) 与 [Docker Desktop 安装说明](https://docs.docker.com/desktop/setup/install/windows-install/) 完成。管理员 PowerShell 中安装 WSL/Ubuntu，按系统提示安排重启；安装并启动 Docker Desktop，启用 Linux 引擎与 WSL2 后端。没有环境验收结果时，不能把此步骤勾为上线。

已准备 `deployment/install-prerequisites.ps1`：在管理员 PowerShell 中运行，会开启虚拟机/WSL 功能并用 winget 安装 Microsoft WSL 和 Docker Desktop。脚本不会发出重启命令；系统要求重启时由你安排。之后运行 `wsl --install -d Ubuntu` 完成 Linux 用户初始化，再启动 Docker Desktop。

## 启动固定版本容器

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deployment/hummingbot/start.ps1
```

Linux/WSL 中可执行：

```console
docker compose -f deployment/hummingbot/compose.json config --quiet
docker compose -f deployment/hummingbot/compose.json pull
docker compose -f deployment/hummingbot/compose.json up -d
docker compose -f deployment/hummingbot/compose.json exec -T hummingbot hbot --version
```

容器是等待命令的 hbot 主机，日志、配置与撮合数据保存在 `runtime/hummingbot/`。不开放端口，也不包含交易所凭据。

## 创建纸面做市样例

Hummingbot 首次使用的本地加密口令由你在终端输入，勿写入 Git 或发到聊天中。使用以下固定的纸面 connector：

```console
docker compose -f deployment/hummingbot/compose.json exec hummingbot hbot create simple_pmm --name paper_btc.yml --set exchange=kraken_paper_trade --set trading_pair=BTC-USD
docker compose -f deployment/hummingbot/compose.json exec hummingbot hbot start paper_btc.yml
docker compose -f deployment/hummingbot/compose.json exec hummingbot hbot status
docker compose -f deployment/hummingbot/compose.json exec hummingbot hbot history
```

这是 Hummingbot 内置做市样例，用于检验公开订单簿、模拟挂单、成交和日志链路。它不等同于仓库 MA20/60 策略，也不是第三周候选策略已迁移上线的证明。纸面余额与交易费用以 Hummingbot 自身配置和记录为准，不能直接与研究端固定滑点模型合并。

验收时保留版本、connector、status、history 和至少一笔模拟成交记录；若市场尚未触发成交，先确认正常报价并继续观察，不能虚构成交。

## 停止

```console
docker compose -f deployment/hummingbot/compose.json exec hummingbot hbot stop
docker compose -f deployment/hummingbot/compose.json down
```

此操作保留 `runtime/hummingbot/` 下的日志与配置。系统运行验收、MA 策略迁移和持续观察仍需各自验证。
