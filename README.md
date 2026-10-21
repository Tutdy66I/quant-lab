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
python -m unittest discover -s tests -v
```

固定方案见 `docs/history_protocol_v1.json`：既有 11 个候选、252/63 滚动验证、相同费用和滑点；以自然年比较阶段，不扩大搜索范围。查看[长期历史验证报告](docs/长期历史验证报告.md)和[数据运行说明](docs/历史数据集.md)。原始数据版本禁止覆盖；重采集必须使用新的 `--dataset` 目录。

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
