# quant-lab

纸面交易机器人实验室：四周从零搭出一只可解释的纸面交易机器人。

> 全程模拟交易，不连接真实账户，不发起真实下单。

## 结构

```
quant-lab/
├── docs/        # 四周建造计划、复盘文档
├── notebooks/   # 回测 notebook（真实历史数据 + 中文解读）
├── strategies/  # 策略代码（每笔信号可解释）
└── data/        # 历史数据与回测结果（CSV/JSON）
```

## 进度

- [x] 第一周：仓库骨架 + BTC 双均线回测（见 `docs/四周建造计划.md`）
- [ ] 第二周：Hummingbot 纸面交易上线
- [ ] 第三周：策略迭代（首轮可复现实验已完成，纸面迁移待验证）
- [ ] 第四周：复盘

## 基线回测结果（BTC MA20/60，2024-12-24 ~ 2026-10-13）

| 指标 | 数值 |
|---|---|
| 策略收益 | +2.29% |
| 买入持有 | -12.51% |
| 交易笔数 | 6 |
| 胜率 | 33.3%（6 笔已完成交易中 2 笔盈利）|
| 最大回撤 | 36.36% |

前日收盘确认信号，次日开盘按单边 0.1% 手续费、0.05% 不利滑点成交；买入持有扣同样费用。原始快照末行缺少已收盘证据，保守剔除。回撤包含初始资金和期末结算费用；6 笔自然退出用于胜率，另有 1 笔期末结算。旧版 +3.4% 已被这一口径替代。

这份历史样本的回撤较大、交易较少，尚不足以判断策略是否稳定有效。脚本和 `notebooks/btc_ma_cross_backtest.ipynb` 均调用研究模块中的同一个回测引擎。

## 策略实验与验证

需要 Python 3.10+，只使用标准库。在仓库根目录运行：

```console
python -m strategies.ma_cross_backtest
python -m strategies.research
python -m unittest discover -s tests -v
```

实验比较 9 组均线参数，以及基线的收盘亏损退出、波动过滤两个改进方向。使用前一日收盘信号、次日开盘模拟成交，并扣双边手续费和滑点。所有候选共享预热区间，按时间 70% / 30% 拆分训练与留出数据。

- [实验报告](docs/策略迭代报告.md)：假设、对比结果、局限与下一步。
- `data/param_sweep.json`：版本 2 的结构化结果，含数据指纹、选参规则和区间指标；取代原先不可复现的结果列表。
- `data/research_trades.csv`：每个候选的训练及留出成交明细、费用和退出原因。

首轮结果：训练区间选出的 MA20/90 在留出区间亏损 15.13%，同段 MA20/60 基线收益为 +17.34%。这些数据此前已被查看，属于回顾性验证，不能视为前瞻实测；完整计算口径见报告。该实验与基线共享成交和费用模型，但使用统一预热、独立训练及留出区间，收益不应直接拼接比较。

## Quant Lab 2.0：策略稳健性研究

PR #4 已统一历史基线、Notebook 与共享回测引擎。下一阶段优先研究 Walk-forward 滚动验证与策略风险，不自动调整纸面账户。

```console
python -m strategies.walk_forward
python -m strategies.robustness
python -m unittest discover -s tests -v
```

首版固定 252 天滚动训练、63 天验证，沿用 11 个候选；只按此前训练数据选参，比较固定基线、分窗买入持有及成本翻倍压力。报告包含逐窗收益、回撤、水下期、最差单日和尾部日收益，以及固定候选敏感性诊断。

- [Walk-forward 风险报告](docs/Walk-forward风险报告.md)：研究协议、第一轮结果和局限。
- `data/walk_forward.json`：数据指纹、训练选择、逐窗交易与结构化风险指标。
- [稳健性对照](docs/Walk-forward稳健性对照.md)：滚动/扩展训练、126/252 天初始训练及 31/63/126 天验证，共 12 个固定协议；全部对齐到相同验证日期，展示零交易窗、成本压力和连续固定基线，避免将分窗清仓影响误作选参优势。结构化结果见 `data/walk_forward_robustness.json`。

单独检查扩展训练窗时可运行 `python -m strategies.walk_forward --window expanding --output-root <独立输出目录>`，避免覆盖默认报告。此模式保持首次训练起点，后续逐窗纳入已经结束的历史区间；选择时仍不读取当窗未来数据。历史快照已被查看，多组窗口比较仅为回顾性诊断，不自动挑选最佳窗口。

各窗空仓开始、期末扣成本结算，按精确权益复投；这份分段研究不能与连续账户收益直接比较。下一步增加来源可核验的更长历史与市场区间，并在新增数据上按预先固定的协议评估。现有原生 Python 纸面观察器继续独立运行，规则保持不变，Hummingbot 继续暂停。

## 长期历史数据与固定验证

独立数据集 `data/history/binance_btcusdt_1d_v1/` 保存 Binance 现货 BTC/USDT 的 2018-01 至 2026-09 日线、105 个原始月包、官方 SHA-256 校验文件及逐包采集记录。它与原 Kraken BTC/USD 快照分别研究，不替换旧数据或模拟账户行情。

```console
python -m strategies.history verify
python -m strategies.history_study
python -m strategies.cross_source verify
python -m unittest discover -s tests -v
```

固定方案见 `docs/history_protocol_v1.json`：既有 11 个候选、252/63 滚动验证、相同费用和滑点；以自然年比较阶段，不扩大搜索范围。查看[长期历史验证报告](docs/长期历史验证报告.md)、[第二来源交叉核验](docs/第二来源交叉核验.md)和[数据运行说明](docs/历史数据集.md)。Coinbase BTC/USD 对照覆盖全部日期，差异告警原样保留，不混入主数据。原始数据版本禁止覆盖；重采集必须使用新的 `--dataset` 目录。

## Quant Lab 2.1：衍生品市场状态研究

公开只读采集、市场结构报告及独立统计验证均已实现，覆盖现货日线、USDⓈ-M OI、资金费率、全市场多空账户比和主动买卖量。版本化保存原始响应、来源、时间及 SHA-256；离线校验并重放 JSON/Markdown 报告。独立固定 252/63 天验证比较价格基线与增加衍生品指标后的波动、回撤和趋势持续性预测误差。详见[衍生品市场研究](docs/衍生品市场研究.md)。

```console
python -m strategies.derivatives_data collect --days 28
python -m strategies.derivatives_data verify <快照目录>
python -m strategies.market_structure <快照目录> --output <新报告目录>
python -m strategies.derivatives_study --output <新研究目录>
python -m strategies.derivatives_pipeline
```

实际可用时间不早于采集时间，回补数据不进入更早的历史决策。当前本地 API 返回地区限制 HTTP 451，失败证据已保存，[初始统计状态](data/derivatives/study_v1_initial/study.md)明确为数据不足、0 个验证样本；尚无预测价值结论。已启用独立 Codex 自动任务，每天香港时间 08:15 采集并研究，运行文件仅写入 `runtime/derivatives/`，相同未变故障不重复通知。该模块不修改现有历史数据、原验证协议或纸面账户。

## Quant Lab 2.2：官方归档可行性验证

第一阶段报告见 [Binance 归档可行性验证](docs/Binance归档可行性验证.md)。已保存 BTCUSDT USDⓈ-M metrics、资金费率、交叉核对样本、官方校验文件及采集指纹。当前样本未复现 Issue #491 的日期切换主张；各字段真实时间与发布可见性仍有待确认，本阶段不接入归档适配器，不改变既有研究及自动任务。

第二阶段已实现 [独立离线标准化与数据验证](docs/归档标准化与独立数据验证.md)：只接纳两个精确版本中已验证的 taker 比率，其他字段及日期/版本隔离；排序、去重、缺口与双层 SHA-256 校验后生成描述性统计。首版 576 个窗口，不进入历史预测或纸面交易。

```console
python -m research.archive_normalizer verify --output data/archive_normalized/binance_um_btcusdt_v1
python -m research.archive_normalizer build --output <新的独立输出目录>
```

第三阶段 [可信覆盖扩展验证](docs/可信归档覆盖扩展验证.md) 已核验 27 个日期、7776 个 taker 窗口，最长连续覆盖 16 天；发现 2021 样本与 2025/2026 样本的内容时间解释不同，逐文件绑定指纹，不硬编码历史切换日期。OI/多空比的独立核验仍缺合格参考证据，继续隔离；连续性与历史可见性尚不满足统计模块准入。

```console
python -m research.archive_coverage verify --output data/archive_normalized/binance_um_btcusdt_coverage_v1
```

## Quant Lab 2.3：前瞻数据证据与持续采集

[实施设计](docs/QuantLab2.3实施设计.md)与[真实验收报告](docs/QuantLab2.3持续观察验收.md)。独立观察器按内容哈希保存原始字节，记录增量观察、首次见证、304 确认、缺口与修订；离线重放校验，沿用精确 taker 契约，OI/多空比保持隔离。实采两轮：2 个 metrics 日包、576 个窗口，10 月 22 日为 404 缺口；新增的是观察证据，尚无前瞻预测样本或官方首次发布证明。

```console
python -m research.archive_observer collect --root runtime/archive_observer_v1
python -m research.archive_observer watch --root runtime/archive_observer_v1 --interval-seconds 3600
python -m research.archive_observer verify --root data/archive_observation/binance_um_btcusdt_v1 --output data/archive_observation/binance_um_btcusdt_v1_snapshot
```

`watch` 为前台循环，本轮未启用后台任务，也未修改现有自动化；详细运行边界及故障处理见验收报告。

## Quant Lab 2.4：Windows 归档运行管理

新增独立单轮运行包装、故障恢复、JSON/Markdown 健康状态和 Windows 计划任务定义，复用 2.3 观察器。默认每小时、登录后触发，避免重复运行；403/451、许可或完整性错误不自动解除。暂时 404 记录为缺口，网络故障按周期有限恢复。

```console
python -m deployment.archive_service initialize --root runtime/archive_observer_v1
python -m deployment.archive_service run --root runtime/archive_observer_v1
python -m deployment.archive_service health --root runtime/archive_observer_v1
```

2026-10-23 本机已确认启用 Windows 任务 `quant-lab-archive-observer`；旧 Codex 归档任务已改为只读监测。首次确认启用为香港时间 11:17:28，7/28 天观察到期分别为 10 月 30 日、11 月 20 日同一时刻；尚未通过长期验收。仓库文档为带日期记录，当前任务状态仍须本机核对。见 [Windows 部署维护手册](docs/QuantLab2.4Windows运行手册.md)和[测试及真实运行证据](docs/QuantLab2.4运行验收.md)。

离线验收报告不采集、不更改任务、不改写健康文件，默认只向 stdout 输出：

```console
python -m deployment.archive_acceptance --days 7
python -m deployment.archive_acceptance --days 28 --output runtime/archive_acceptance/review-28d-unique
```

报告区分采集、限频跳过、失败和中断；未知触发不推断为自动，缺少完整调度历史时完整成功率保持空。到期只进入人工评估，不自动判通过。单轮包装在冷却结束前至多等待 60 秒，避免小时触发早几秒就跳过整个周期，仍保持完整一小时冷却。

## 纸面运行状态

当前持续运行原生 Python 观察器；Docker / Hummingbot 已按用户要求暂停。查看[最新观察汇总](docs/纸面观察进度.md)及[持续观察计划](docs/持续观察计划.md)。汇总是带时间戳的定时快照，不是实时状态页。

```console
python -m strategies.paper_trader
python -m strategies.paper_trader --loop
python -m strategies.paper_trader --status
python -m strategies.paper_trader --stop
```

V2 观察器使用与研究脚本共用的 MA20/60 信号、次日开盘成交模型、单边 0.1% 手续费和 0.05% 滑点。`--loop` 启动立即检查，以后每天 UTC 00:10（香港时间 08:10）检查，失败后每 60 秒重试。

账户、成交、每日权益和运行记录以 `runtime/paper_v2/paper.sqlite3` 为准；JSON/CSV 是可重新生成的视图，无成交日也写入权益与健康记录。行情快照包含来源、采集时间、最后已收盘日期和指纹；过期、缺失或不连续数据会阻止记账。`--status` 在检查逾期或出错时返回非零退出码。

当日未收盘权益会标记为暂定，次日自动补齐最终收盘估值。停机补跑带有观察时间和延迟秒数，成交类型明确标为 `daily_open_model`；这是公开日线的模拟账本，不能声称按当时开盘价实际成交。

V2 从新的模拟账户开始，旧版 `data/paper_state.json` 与旧成交日志保留作历史资料，不能混入新费用和成交模型。运行文件位于已忽略的 `runtime/` 中。

Windows 后台运行与登录自启见 [运行手册](docs/纸面运行手册.md)。Hummingbot 独立撮合环境见 [部署说明](docs/Hummingbot纸面配置.md)，持续观察和四周实测复盘需要累积新增数据。
