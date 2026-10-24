# Quant Lab 2.5：运行证据与验收闭环

本版本仅修改独立运维层。研究观察器、语义注册表、数据指纹、准入规则、252/63 历史研究协议和纸面账本保持不变。不启用 Hummingbot、不连接交易账户、不下单。

## 成功时钟与故障诊断

健康报告增加 successful_collection_age_seconds 和 longest_successful_collection_interval_seconds。失败、限频跳过和运维调用均不推进成功时钟；超过三小时仍告警。旧 longest_collection_interval_seconds 保留原定义，供旧消费者兼容，不能替代新指标。没有成功采集时年龄为空，并报告 no_successful_collection。

latest_failure_diagnostic 仅分类已保存的失败字节：TLS 证书、DNS、超时、其他网络故障、访问限制、服务器暂态故障或来源/完整性停止。它不发请求、不暴露原始异常文本、不修改原采集日志或恢复规则。解释器版本仅表示生成诊断的解释器，不能反推历史采集解释器。包装、验收、诊断和历史解析模块均记录独立代码指纹；旧回执不补写。

2026-10-24 只读检查发现最近一次条款请求的已保存异常为证书验证失败并包含 self-signed certificate。该证据不能区分代理、CA 配置和其他信任链原因，也不能证明全部夜间空档由 TLS 引起。不得通过关闭验证、自动信任未知证书或换代理绕过。修复信任配置和再次联网验证需另行给出具体方案，不属于本次开发动作。

## 离线任务历史

可选 --scheduler-events 接受 UTF-8 Windows TaskScheduler Operational XML（Events 包含 ToXml() 输出），最多 8 MiB，拒绝 DTD/实体、格式错误和冲突记录。不访问任务计划程序，不自动启用系统日志。

```console
python -B -m deployment.archive_acceptance --days 7 --scheduler-events exported-events.xml
python -B -m deployment.archive_task_history exported-events.xml --start 2026-10-23T03:17:28Z --end 2026-10-30T03:17:28Z
```

仅读取指定 provider、精确任务名和检查区间。事件 107、110、119 分别记录时间、用户、登录触发；201 保存动作返回码。映射依据本机 Microsoft-Windows-TaskScheduler provider 的事件定义核对。用户触发不能证明操作者身份。按 record ID 去重；导出应来自同一机器同一日志，不拼接不同日志代际。实例 ID 供人工核对；时间接近不能证明与某回执关联，不改写历史 trigger_kind。

导出始终标记 partial_or_unknown：没有日志启用、保留及丢失证据，不能建立完整触发分母。task_execution_success_rate 和 schedule_punctuality_rate 继续为空。事件 102 或退出码 0 均不等于采集成功。完整分母和可证明的回执关联仍待后续实现；现有缺失历史不能补造。

## 7/28 天人工验收门槛

原部署起点保留为香港时间 2026-10-23 11:17:28；7/28 天最早到期分别为 10 月 30 日、11 月 20 日同一时刻。不得重置起点以隐藏异常。后续完整事件证据区间须另行注明。

review_criteria 逐项输出 pass、fail、not_due、unverified、not_assessable：

- 观察期到期、全部快照完整性通过；失败不被较新的 healthy 覆盖。
- 任意超过三小时的成功采集间隔（包括末尾开放区间）、最近采集停止或没有启用后成功、48 小时逾期归档缺口均阻碍验收；解释原因不自动豁免。
- 调度分母、真实登录/重启恢复、真实来源修订处理，缺证据时保持未验证或不可评估。
- 统计模块必须关闭；未确认字段继续隔离，不以运行验收替代字段准入。
- 28 天容量趋势缺乏可比较测量时保持不可评估；部署体积基线缺失时增长量仍为空。

存在 fail 时 decision=blocked，否则仍为 evidence_incomplete。此版本不提供自动通过路径，long_run_acceptance 始终 not_established。人工签署和豁免机制不在本版本范围。

报告新增完整运维轮次耗时与 total_report_seconds；offline_replay_seconds 仍为原核心重放计时，二者不得混用。容量当前测量不冒称部署时测量。

## 测试与部署边界

离线测试覆盖失败及跳过不重置成功时钟、错误分类不泄露异常文本、历史类型/返回码/去重/范围、错误 XML、缺分母保持空、未到期和失败门槛。冻结协议和账本不进入测试写入范围；真实目录仅做只读重放并比较文件哈希。

真实登录、重启、睡眠、电池、故障后定时恢复和长期连续性仍需要自然运行或单独批准的演练。本次不注册、重启或更改 Windows 任务，不启用 Operational 日志，不修改 CA/代理或远端状态。注意现有任务直接加载本地源码：运维代码保存后会在后续自然触发时被使用，无需重注册任务；这不构成后续自然触发已经成功的证据。

## 后续授权与环境对照：2026-10-24

初次开发阶段没有修改日志设置。随后用户明确授权启用 TaskScheduler Operational 日志，管理员 wevtutil 命令退出码 0；香港时间约 11:18:57 首次复核 IsEnabled=true，容量 10 MiB。这只建立后续日志记录条件，不补回既有历史，不改变任务、触发器、恢复次数或采集预算。环形日志将来可能覆盖，导出仍不自动获得完整分母资格。

当天正常主机环境下，任务实际使用的 Python 3.13.14 与 bundled Python 3.12.14 各对既有条款地址发送一次 HEAD 请求，均在默认 TLS 验证开启下返回 200。沙箱中 bundled Python 可见证书数量、代理类型和网络权限与正常主机不同，受限探测返回 PermissionError。这不能反推历史失败进程环境，但说明目前没有修改系统 CA 或关闭验证的依据。HEAD 连通性不等于采集成功，不写入原观察账本。

新回执增加 execution_context：当前进程的 Python/OpenSSL 版本、默认证书库存计数、代理类型名称，不记录代理地址或凭据。旧回执解释器仍未知，禁止用新环境覆盖历史原因。

`deployment/archive-task-history.ps1` 只读取已启用日志，向 stdout 输出目标任务的 XML；无匹配事件输出空 Events，不启用日志、不运行任务。可将输出保存到全新文件后交给离线解析器（使用 UTF-8），MaxEvents 最多 10000，导出不能证明未截断或无历史丢失。默认检查最近一天。

香港时间 11:21:59.521880 自然产生事件 107（时间触发），同实例随后产生 100/200/201/102，11:22:02 完成，动作返回码 0。包装回执记录实际 Python 3.13.14、OpenSSL、90 个证书和代理类型。本轮尚早于 11:28:49 最早采集时刻，返回 not_due，无新 HTTP；成功采集过期告警保留。下次计划为 12:21:58，尚不能将本轮称为采集恢复。

新增事件导出仅保存到 runtime/archive_observer_monitor/ 的新文件，离线解析取得 6 条目标任务事件；完整验收重放 18 个快照通过，重放前后观察及运维文件哈希清单一致。完整 Python 3.13 回归 159 项通过；最终新增导出过滤测试所在的 Windows 任务测试 3 项通过。代码尚未提交或推送，既有纸面文档改动保持。
