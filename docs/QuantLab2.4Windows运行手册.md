# Quant Lab 2.4：Windows 归档运行手册

本版本增加 Windows 单次运行管理、离线健康报告和任务定义，复用并保留 2.3 的 `research/archive_observer.py`。MA20/60、固定验证协议、纸面账户和其他自动任务不变。不使用交易账户，不提供下单功能，不新增研究模型。

## 部署前提与调度选择

Python 3.10 以上；在仓库根目录执行命令。推荐给任务提供固定的 Python 绝对路径，避免依赖交互式终端的 PATH。运行文件和本地任务配置位于已忽略的 `runtime/`，不得提交 Git。

[Binance 官方说明](https://github.com/binance/binance-public-data/blob/master/README.md)称日归档在次日提供，但未在该说明中给出具体 UTC 发布时间或 Vision 下载配额。因此使用本地保守策略：**每小时单次运行，单轮最多 25 次请求**；已取得对象按 2.3 的最近日期/24 小时规则复查，404 至少一小时再查。每小时并非官方速率承诺，也不能把 404 当作官方宣布未发布。预算不足时下一轮继续补采，禁止无界追赶。

任务名称 `quant-lab-archive-observer`，定义见 `deployment/archive-service.ps1`：

- 每小时触发；当前用户登录一分钟后也触发。安装后的首个时间触发在五分钟后。
- `StartWhenAvailable`，`IgnoreNew`，低优先级 7，单次上限 20 分钟；不启动常驻终端。
- 当前用户交互登录令牌、最低权限，不保存账户密码、不提权、不更改电源策略，不设置唤醒。屏幕锁定后仍可运行；注销、关机和睡眠期间不保证采集，登录/恢复后补跑。
- 默认电池供电时不启动；已开始的轮次不会仅因切换电池被停止。离线时也启动，以便如实写网络错误。
- 不设置任务计划程序的快速重试，避免绕过应用层访问停止策略。正常恢复留给下一个小时周期。

[Microsoft 设置说明](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtasksettingsset)、[触发器说明](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtasktrigger)。这是登录后恢复方案，不保证登录前的无人值守运行。若将来需要注销后运行，应单独评估服务账户与凭据，不默认改用 SYSTEM。

## 离线接管与预览

2026-10-23 本机已启用归档 Windows 任务，旧 Codex 归档任务已交接为只读监测；不要重复 Install。以下接管步骤用于新环境或经确认的迁移，先检查实际调度器和进程。当前观察目录为 `runtime/archive_observer_v1`。**实际切换前必须先确认旧采集调度停止/改为只监测**，不能让它与 Windows 任务并行采集。其他纸面及衍生品任务不属于本次切换范围。

```powershell
python -m research.archive_observer verify --root runtime/archive_observer_v1
python -m deployment.archive_service initialize --root runtime/archive_observer_v1
python -m deployment.archive_service health --root runtime/archive_observer_v1
$archivePython = (Get-Command python).Source
.\deployment\archive-service.ps1 -Action Plan -PythonExe $archivePython
```

`initialize` 只对现存观察日志建立独立基线，不联网、不覆盖原始数据；重复调用只核验。全新环境需先用 2.3 的 `collect` 创建独立数据目录并保留首次真实采集证据，再初始化。初始化中断留下部分基线时停止人工检查，不自动覆盖重建。

`Plan` 只输出 XML，不注册、不启用任务。原生 Task Scheduler COM 已用于解析该定义，解析不是任务实际运行证明。任务的执行命令使用固定工作目录、带引号的绝对路径和 `RemoteSigned`；安装前可人工检查 XML。

## 经用户确认后启用

以下操作仅用于尚未部署的新环境，会注册并启用新任务，必须先向用户说明周期与系统影响并取得确认；已有同名任务不得重复注册：

```powershell
# 先完成旧归档调度器的人工交接。两个开关表示操作者确认，不是自动探测结果。
.\deployment\archive-service.ps1 -Action Install -PythonExe $archivePython -ConfirmEnable -PreviousCollectorStopped
.\deployment\archive-service.ps1 -Action Status -PythonExe $archivePython
```

已有同名任务时安装拒绝覆盖。要变更计划，先导出旧 XML、检查最后状态并明确迁移，不使用 `-Force` 覆盖。任务创建本身不会启动应用常驻进程，之后按触发器执行单轮。

单次手动运行、离线健康检查、停用和卸载：

```powershell
.\deployment\archive-service.ps1 -Action RunOnce -PythonExe $archivePython
.\deployment\archive-service.ps1 -Action Health -PythonExe $archivePython
.\deployment\archive-service.ps1 -Action Disable -PythonExe $archivePython
.\deployment\archive-service.ps1 -Action Remove -PythonExe $archivePython
```

Disable/Remove 只停止未来触发，不删除数据、不终止其他进程。若单轮尚在运行，等待正常结束；强制结束可能留下锁与未提交字节，需要下一轮恢复检查。程序不会盲目删除锁。

## 状态与保留规则

观察数据仍在 `runtime/archive_observer_v1/`；新增运维目录为 `runtime/archive_observer_v1_service/`：

| 文件 | 用途 |
| --- | --- |
| `baseline.json`、`snapshots/baseline/` | 显式接管时间、原始日志头、代码/语义注册表指纹、离线快照 |
| `cycles/` | 按序号和 SHA-256 命名的不可覆盖运维回执，含前一回执哈希、实际起止、结果、恢复信息和检查点 |
| `snapshots/<唯一标识>/` | 每轮固定快照，保持历史观察及版本可重放 |
| `health.json`、`health.md` | 原子替换的机器状态及可读报告；不是原始事实账本 |
| `running.json`、`service.lock` | 当前运行意图和操作系统互斥锁；进程退出后 OS 释放互斥锁 |
| `emergency/` | 运维回执链无法写入时的新故障证据，不覆盖损坏链 |

`latest_successful_collection_at_utc` 是最近无停止原因的采集轮次结束时间，包含仅确认已有文件的轮次，不表示新增样本。`latest_metric_version_first_observed_at_utc` 则是当前 metrics 版本最早实际观察时间。报告另含最近运维轮次、真实覆盖区间、行数、已核验 taker 窗口、缺口、文件内时间网格问题、修订、孤立字节和异常原因。

所有时间为 UTC。首次观察不是官方发布时间；`official_publication_at_utc` 无证据时保持空。新日期/新版本即使 SHA-256 正确，也须按原有精确时间语义规则隔离；OI 和多空比例不会被运行层自动准入。所有回补保持回顾性，前瞻预测样本始终为 0，不调用统计模块。

健康状态可能为 `healthy`、`degraded`、`retry_pending`、`busy`、`running_or_interrupted`、`collector_running_or_stale_lock` 或 `manual_review_required`；`healthy` 仅表示本轮检查无阻碍，**不代表长期稳定验收通过**。`run` 成功/未到期/重复启动返回 0；采集失败、人工复核或运维异常返回 2。`health` 完整性或人工阻碍返回 2，其余状态读 JSON 进一步判断。原生计划任务历史/LastTaskResult 可检查退出码；本版本不发送邮件、消息或配置外部告警服务。

## 故障分类与维护

| 故障 | 行为 |
| --- | --- |
| 404 | 保留响应，记录缺口，后续周期再查；完整 UTC 日结束 48 小时后仍缺失则标记逾期。这是本地告警阈值，不是官方 SLA |
| 超时/连接错误、HTTP 500/502/503/504 | 本轮立即停止，至少间隔一小时才可恢复；最多连续 3 个失败采集轮次，然后要求人工检查 |
| HTTP 403/451/429/401、其他状态、跳转、许可变化、无效 304 | 停止请求，不自动解除，不换源、代理或密集重试 |
| 本地字节/快照/日志损坏、代码或注册表变化、时钟倒退 | 停止联网，健康状态不得沿用旧成功覆盖假装验证通过 |
| 崩溃留下 collector.lock | OS 运维锁先保证单实例；只有 PID 可证明已经退出且离线重放通过，才删除不变的旧 collector 锁。PID 存活/复用、无法查询或内容无效均不删除 |
| 未提交对象 | 保留并检查每个 SHA-256，只列为孤立字节，不能据此建立首次观察时间 |
| 超过 3 小时未完成采集 | 标记运行空档；电脑停机期间不能实时写告警，恢复后如实记录间隔，不补造按时观察记录 |

运维锚点核对 2.3 代码指纹、语义注册表、快照输出哈希和截止时间内的完整回放。惟独全局“孤立字节库存”可以因未来崩溃而变化：仍逐字节校验，但不把库存变化解释为历史快照事实变化。这项区别只在独立运维层处理，2.3 原验证器保持不变。

修复运维异常后，可以人工记录检查原因（不联网、不解除来源限制）：

```powershell
python -m deployment.archive_service review --root runtime/archive_observer_v1 --reason "已人工检查进程并修复具体故障，离线完整性核验通过"
```

对于访问限制/许可/官方校验问题，须先取得实际允许访问和合法性/完整性证据，再由人工明确决定是否使用 2.3 的 `--resume-after-review`；定时任务从不自动解除。升级代码/语义注册表时，保留旧运行目录和检查点，版本化迁移，不绕过锚点校验。

备份整个观察目录、运维目录及原始快照到可靠存储。不要只备份最新 health 文件；本地自洽哈希链不能独立证明整套目录未被回滚/删除。定期核对离线备份的日志头，避免将最新目录误当外部历史可用性证明。快照和日志默认持续保留，不自动清理；磁盘用量与重放耗时需在长期观察中测量，若接近 20 分钟上限应先复核规模，不能简单缩短校验范围。

## 长期验收

经确认启用后，从实际启用时间分别观察 7 天和 28 天，不以前期手动样本冒充自动运行时长。至少核对真实触发/完成记录、退出码、最长间隔、登录恢复、停机补采、缺口恢复、HTTP 限制停止、校验结果、修订及隔离字段。测试模拟和 COM 定义解析分别标明；本机真实重启/登录触发、跨天自动触发、停机恢复和长期连续性尚未实测时必须保持“未验证”。

## P0：小时边界与独立验收报告

任务仍每小时触发。包装层以上轮采集结束加一小时为最早运行时刻；若只差 60 秒以内，在 OS 锁内等待并重查时钟，不缩短冷却。更早的登录触发返回 not_due。慢请求或系统空档仍可能延后实际采集，不能承诺每小时精确新增数据。包装不更改现有任务定义。

`running.json` 附加阶段（cooldown、collecting、request、snapshot）与阶段时间；request 阶段仅记录公开来源 URL。异常退出后下一轮保留该运行意图和死亡锁恢复信息；阶段不能证明进程为何终止。新回执附带运维包装代码指纹，旧回执不补写。2.3 观察器代码及原锚点保持不变。

```console
python -m deployment.archive_acceptance --root runtime/archive_observer_v1 --days 7
python -m deployment.archive_acceptance --root runtime/archive_observer_v1 --days 28 --output runtime/archive_acceptance/review-28d-unique
```

默认只读 stdout，不调用采集、health 写入、注册或停止任务。可选输出必须是原观察/运维目录以外的全新目录，已有目录拒绝覆盖。报告失败或并发运行待复核返回 2；成功生成报告返回 0，不表示长期验收通过。

本机部署起点以 deployment.json 的 2026-10-23 香港时间 11:17:28 为准。7/28 天最早到期为 2026-10-30、2026-11-20 同一时刻；较早的手动采集基线原样保留并报告差异。报告覆盖从启用至实际检查的时间，晚执行另列延迟，不提前验收，不自动判通过。若未来迁移，应另存新部署证据而非覆写旧起点。

验收需审阅全部可见运行、正常跳过、失败、中断、成功采集最长空档（含最后一次成功至检查时刻）、超过三小时的空档、完整 UTC 日结束 48 小时后的缺口、404 恢复、修订、隔离字段、全部快照与日志完整性。操作回执完成率仅针对已观察到的终态回执；busy/reviewed 等另列，不冒充任务成功率。OS 锁拒绝的重复启动不写回执，无法完整统计拒绝次数。触发来源未知不按整点推断；缺少 Task Scheduler 完整历史时全量任务成功率和准时率保持 null。

28 天还要比较带真实测量时间的磁盘文件数/字节数、采集耗时和完整重放耗时。没有部署时体积基线时增长量保持 null，不能把第一次后补测量当作部署测量。报告不写回或重置监测基线。没有实际发生的登录/重启恢复、来源修订和故障仍为未验证；原始字节完整性不代替统计准入或外部公证。
