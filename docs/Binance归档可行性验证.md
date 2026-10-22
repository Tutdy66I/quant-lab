# Quant Lab 2.2 第一阶段：Binance 官方归档可行性验证

验证日期：2026-10-22（香港时间）。基于远端 main `86b31b2c12340a08334c5f193c4eb5b1b88cc769`，分支 `codex/binance-archive-feasibility`。

## 结论与阶段决策

**公开归档下载和离线校验可行；当前证据不足以把所有 metrics 字段当作历史时点可用特征。第一阶段止于报告和证据，不实现或接入归档适配器。**

BTCUSDT USDⓈ-M 的 daily/metrics 和 monthly/fundingRate 均正常返回 HTTP 200，12 个实际 ZIP 全部匹配官方 SHA-256。目录有连续日期对象，不等于已经验证全部历史内容。当前 BTCUSDT 样本**未复现** Issue #491 声称的 2026-06-25 标签切换或跨文件午夜重复，不能据此给旧数据统一平移五分钟。OI 和多空比的真实观测时点仍未被独立确认，发布时点也不能由行标签推导。

本次只新增 `research/binance_archive_phase1.py`、本报告、隔离的证据目录和离线证据测试。未修改策略、MA20/60、固定 Walk-forward 协议、纸面账户、行情来源或自动任务；没有真实账户连接、密钥或下单接口。引用对话的工具返回仅含内容引用占位符，未取得实质讨论内容，故以本次用户要求为范围。

## 来源、访问和许可

- [官方公共数据仓库](https://github.com/binance/binance-public-data)：说明公开下载、校验与归档可能修订。
- [Vision metrics 目录](https://data.binance.vision/?prefix=data/futures/um/daily/metrics/BTCUSDT/)；[资金费率目录](https://data.binance.vision/?prefix=data/futures/um/monthly/fundingRate/BTCUSDT/)。官网 HTML 明确指定 S3 为目录清单接口，保存的 `vision_index.html`、`vision_list.js` 证明这一来源关系；ZIP 始终从 Vision 下载。
- [官网数据使用条款 PDF](https://data.binance.vision/Binance_Vision-Terms_of_Use.pdf)与[仓库条款](https://github.com/binance/binance-public-data/blob/master/TERMS_AND_CONDITIONS.md)均标注 2026-08-26、版本 1.0：数据采用 CC BY-NC-SA 4.0，允许非商业研究。本次属于个人非生产研究，保留来源和条款。README 的 MIT 标记不能取代数据许可。证据及衍生分析按同一非商业许可保留，见证据目录 `LICENSE.md`。
- Binance 插件只提供只读 API，没有归档下载能力。本次未调用 fapi/api REST 行情接口，也未把插件当作 HTTP 451 的替代通路。

采集为人工触发的有界请求，不带账户凭据，不切换代理或绕过地区限制；遇 401/403/429/451 或服务端故障即停止，无重试/备用主机。唯一非 200 响应是开发文档和 schema 的 HTTP 202 空体，原样保存；不能算作已采集的字段文档。字段语义另通过浏览器文本读取[官方市场数据文档](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data)核对，未伪造成功下载快照。

## 可用性和覆盖范围

证据目录：`data/archive_feasibility/binance_um_btcusdt_v1/`。`receipts.json` 保存每次请求 URL、最终 URL、状态、响应头、开始/完成时间、原始字节数和 SHA-256；`analysis.json` 是离线复核结果。40 个原始响应均经过本地指纹复核，包括目录 XML、官方校验文件、条款和 Issue JSON。

| 数据 | 当前公开目录对象范围 | 实际内容样本 | 验证边界 |
|---|---|---|---|
| USDⓈ-M daily/metrics | 2020-09-01 至 2026-10-21，2242 个 ZIP，区间内未缺日期对象 | 2021-12-01、2025-06-24、2026-06-23/24/25/26、2026-07-01、2026-10-20，共 8 天，每天 288 行 | 目录存在性完整；内容只抽样，不声称 2242 包均无缺失或异常 |
| USDⓈ-M monthly/fundingRate | 2020-01 至 2026-09，81 个 ZIP（月序列连续） | 2026-06 和 2026-09，每包 90 行 | 已证实月包可获得，不推断 daily/fundingRate 存在 |
| USDⓈ-M daily/klines，5m | 仅取 2026-06-24、25 两包 | 每包 288 行 | 用于独立于 metrics 文件的成交量窗口交叉核对 |

metrics 8 包均无重复标签、无非五分钟网格标签，数值有限且非负。2026-10-20 原始行序有 31 次倒退、269 个非 300 秒相邻步长；排序后 288 个时点完整，缺点为 0。必须保留原始行序，不能把第一/最后一行误作时间范围，也不能默认来源已排序。

## 字段定义与尚未确认的部分

CSV 有表头，metrics 恰为下列八列。表中含义按名称和相应官方 REST 字段解释；官方公共归档 README 没有给出 metrics 的逐列版本化时间契约，因此 API 字段对应关系属于待进一步确认的映射，而非归档规范保证。

| CSV 列 | 研究含义与单位 | 证据限制 |
|---|---|---|
| `create_time` | 无时区后缀的日期时间标签；本次按官方归档 UTC 约定解释 | 不等于实际发布时间或实际可见时点；不直接当作 `available_at` |
| `symbol` | `BTCUSDT` | USDⓈ-M，不是现货 BTC/USD 或 COIN-M |
| `sum_open_interest` | 总未平仓量；对应 `sumOpenInterest`，BTCUSDT 通常按基础资产 BTC 解释 | API 官方文档只明确 total open interest；本阶段没有独立核对计量单位或瞬时观测偏移 |
| `sum_open_interest_value` | 总未平仓名义价值；通常按报价资产 USDT 解释 | 对应 API 的总价值字段；估值价格口径和归档观测时点未确认 |
| `count_toptrader_long_short_ratio` | 大户多/空账户数量比，无量纲 | 按相应 API 含义映射；不能当作账户数或持仓量 |
| `sum_toptrader_long_short_ratio` | 大户多/空持仓比，无量纲 | 按相应 API 含义映射；不是账户数量比 |
| `count_long_short_ratio` | 全市场多/空账户数量比，无量纲 | 不能解读为全市场净头寸 |
| `sum_taker_long_short_vol_ratio` | 主动买量/主动卖量，无量纲 | 本次仅对两天用成交量窗口验证，不是金额差或净流入 |

资金费率月包为 `calc_time,funding_interval_hours,last_funding_rate`：`calc_time` 是毫秒 epoch 计算标签，`funding_interval_hours` 为小时，费率为无量纲小数，不能直接当百分数。两个样本区间均为 8 小时，但不推广为所有未来合约的固定结算周期。六月首条为 `1780272000001`，九月首条为 `1788220800005`，存在毫秒偏移；原值保留，不取整。归档无 `markPrice`，不能从中还原持仓资金费用金额。`calc_time` 是否与 API `fundingTime` 完全等同尚未逐笔验证。

## Issue #491：真实样本没有复现其日期切换主张

[原 Issue](https://github.com/binance/binance-public-data/issues/491)于 2026-08-24 提出，当前开放；保存了正文与当时全部评论 JSON。本次取得的唯一评论为另一社区用户的 AERGOUSDT 观察，没有 Binance 维护者对 BTCUSDT 时间语义的明确确认。其他币种异常没有推广至 BTCUSDT。

| 核对项 | 2026-06-24 当前包 | 2026-06-25 当前包 |
|---|---|---|
| 标签范围 | 当日 00:00:00 至 23:55:00 | 当日 00:00:00 至 23:55:00 |
| 行数 | 288 | 288 |
| 与另一日相同标签 | 0 | 0 |
| ZIP SHA-256 | `6bb5ab473189fa7c935b4d9a58d34b2c96cc329cd74fd6f9f17f8abc48ece649` | `56e0751dabd8108d61d1f9de86931ac68e901dad202d4dbac03173e2768be68e` |
| HTTP Last-Modified | 2026-07-29 06:12:20 UTC | 2026-07-28 04:26:19 UTC |

两包均匹配同目录官方 `.CHECKSUM`。Issue 未提供其 BTCUSDT 对象指纹，因此无法证明引用者当时读取的是相同字节，也不能证明他们当时必然错误。Last-Modified 只说明当前对象元数据，不证明原始发布、修订原因或 Issue 前后版本沿革；不得臆称官方已修复。

另外用官方 5m K 线的 `taker_buy_volume / (volume - taker_buy_volume)` 重建每窗主动买卖量比，比较 metrics 标签 T 在 `[T-5min,T)` 和 `[T,T+5min)` 两个候选窗口的匹配：

| 日期 | 前一窗口 r / MAE / 对数 | 当前开始窗口 r / MAE / 对数 |
|---|---|---|
| 2026-06-24 | 0.146890 / 0.432467 / 287 | 0.999703 / 0.002794 / 288 |
| 2026-06-25 | 0.042781 / 0.452101 / 288 | 0.999381 / 0.002690 / 288 |

前一窗口在 6 月 24 日少一对，因为没有下载前一天的 K 线；不补零。当前版本两天都强烈支持 taker 列按开始标签解释。MAE 非零，K 线交叉核对不能替代逐笔 aggTrades 精确重构，也不能证明 OI、账户比、持仓比的时点。仍须核对更多历史版本和窗口，禁止由一列外推六列统一偏移。

在线官方 REST 文档仍将相应 OI/多空比 `timestamp` 描述为周期结束时间；这是 API 文档陈述，并非已核验的当前归档契约。此不一致需显式记录，不能靠默认加减五分钟掩盖。

## 数据发生、发布与采集时间

- **事件/标签时间**：保留 `create_time`、K 线 `open_time/close_time`、资金 `calc_time` 原文；另建字段表达经证据支持的聚合区间，不能覆盖原值。本次 taker 两天支持的区间结束为 T+5 分钟，其余指标的有效时间先标记未知。
- **发布时间**：`published_at_utc` 全部为 null。官方 README 的次日/次月可下载说明只是一般发布节奏；S3/HTTP Last-Modified 是当前对象修改时间，HTTP Date 是响应时间，ZIP 内部时间也不作为首次发布证据。例：6 月 24 日 metrics 发生在六月，当前对象七月修改，十月才被本实验实际采集。
- **实际采集时间**：本次请求窗口 UTC 2026-10-22 08:38:58 至 08:40:27，即香港 16:38:58 至 16:40:27；逐文件以 receipt 为准。这批回补数据最早只能在各文件实际采集完成之后对本项目可见，不能回填为 2026 年六月当时已知的决策输入。

SHA-256 证明当前本地字节与取得的发布者校验值一致，不证明字段经济含义、历史发布时间或来源从未修订；ETag 不能替代 SHA-256。后续重采集必须新建版本目录，保留不同指纹及各自观察时间，禁止覆盖或静默选一条冲突记录。

## 复现、验证及后续准入条件

离线检查无需网络或账户；它逐个验证原始响应指纹、ZIP 官方校验值及成员名，生成字段/覆盖/乱序和窗口对齐统计：

```console
python research/binance_archive_phase1.py analyze data/archive_feasibility/binance_um_btcusdt_v1
python -m unittest discover -s tests -v
```

人工复采时使用全新目录：

```console
python research/binance_archive_phase1.py collect data/archive_feasibility/NEW_VERSION
```

这是有界的可行性实验工具，没有策略导入、归档适配器接口或定时集成。开发文档及 schema 的 HTTP 202 空体保留为失败证据，不应据此称字段规范已落盘。

本地最终全套 **84/84 通过**。原有 81 项首次全部通过；加入 3 项离线证据测试后的两次中间运行均为 83 项通过、1 项错误，来自既有 `test_concurrent_checks_commit_each_fill_once`，Windows 临时目录中 `os.replace` 并发替换快照报 `PermissionError: WinError 5`；未修改纸面实现或测试，最终完整复跑通过。三项新增测试分别确认完整证据可确定性回放、原始响应损坏被拒绝、即使本地 receipt 一致也会核对发布者校验文件。初次 Linux CI 发现派生 JSON 使用平台换行导致字节回放失败，已固定为 UTF-8/LF；原始响应字节没有改变。GitHub 跨平台最终 CI 结果见本 PR checks。

进入适配器阶段之前需要决定并固定：各字段及历史分段的真实时间契约、未知时点的隔离规则、版本修订和重复冲突处理、发布可见性模型、完整历史内容检查范围。未来接口至少应分离 `source_label`、`event_time/window_end`、`published_at`、`collected_at` 和 `available_at`，不得改变现有策略或把回顾性档案当作前瞻收益证据。本报告先供审阅，本 PR 不跨入下一阶段，也不合并或部署。
