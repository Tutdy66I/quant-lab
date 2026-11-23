# 第 24–39 轮轻量合成能力冻结清单

适用范围为独立工作区内的可信人工合成研发。源码与证据逐文件由第 39 轮清单锁定，本轮验收清单补充第 40 轮新增说明和复核回执。后续维护聚焦正确性、可复现性和边界问题。

| 模块与轮次 | 状态与维护决定 | 输入 → 输出，主要依赖 | 对应证据 |
| --- | --- | --- | --- |
| 独立入口和基础组件 · 24 | 稳定保留。维护固定名单、来源检查和出口状态 | Windows CPython 3.10.11、隔离目录、静态白名单 → 320 项轻量回归与 JSON 摘要。入口 `run_synthetic.py`，使用标准库 `unittest` 和经验证的本地 vendor 组件 | `run-history.md`、`round39-independent-regression.json`、第 40 轮回归 |
| 时序防泄漏、样本与预测谱系 · 25–26 | 稳定保留。维护样本对应、训练可用时点与共同评分域 | 人工数据集指纹、样本序号和时间信息 → `Sample`、`Prediction`、`LossRecord` 与拒绝原因。依赖 `sample_lineage.py`、`leakage_diagnostics.py` | `round25-report.md`、`round26-report.md` |
| 全集承诺、滚动和验证链 · 26–27 | 稳定保留。维护固定总体、缺失检查和数据配置绑定 | `synthetic_manifest27.py` 生成/验证样本清单，`rolling_synthetic.py` 实现滚动，`research_chain27.evaluate_verified` 绑定数据、模型与配对误差 → 人工研究结果 | `round26-evidence-manifest.json`、`round27-evidence-manifest.json` |
| 成对与分块 Bootstrap · 25–27 | 诊断用途。保留实现与假设说明 | 固定合法评分域上的配对损失 → 区间及覆盖敏感性。依赖 `paired_bootstrap.py`、`coverage_synthetic.py`、`bootstrap_robustness27.py` | `bootstrap_robustness_report27.md` |
| 本地证据存储 · 28 | 稳定保留。维护独占写入、四类证据与验证 | 已知根目录、角色标签、源码/配置/日志/结果 → 新 run ID、文件摘要、清单摘要及验证回执。依赖 `evidence_store28.py` 的 `create_run`、`save_artifact`、`finalize_run`、`verify_run` | `round28-report.md`、本轮最小 E2E 回执 |
| 留出准备与审计型评价 · 27–29 | 玩具流程历史对照。原 60 行评分当前禁止 | `holdout_audit28.py` 核对注册与账本，`audited_evaluation29.py` 需要数据、计划、封存、授权、账本和 run ID → 尝试与完成回执。同一所有者能切换或回滚本地状态，严格一次性与独立审批未就绪 | `round27-holdout-protocol.md`、`round29-report.md` |
| 三预测基线 · 25、30、34 | 核心冻结。仅进行缺陷维护 | 人工特征及届时合法到达的标签 → 固定 Ridge、更新训练均值、定期重训 Ridge 的预测和版本记录。依赖 `ridge_synthetic.py`、`prequential_synthetic31._fit`、`engine_five34.py` 与时序适配器 | `candidates34.json`、`round34-lock.json`、`round34-report.md` |
| 漂移与历史自适应 · 30–34 | 原始自适应为历史对照，稳健报警仅诊断。保留不扩展 | 预注册人工序列与已到达反馈 → 漂移、报警、适应性和错误分析。依赖 `calibration_synthetic31.py`、`drift_robust32.py`、`engine_cross33.py` | `round31-report.md` 至 `round34-report.md` |
| 文件内质量与反馈 · 35 | 稳定保留。维护时间、标签及总体核验 | `quality35.py`、`feedback35.py`、`baselines35.py`、`evaluation35.py`，从人工数据、带时间戳的特征/标签包与清单 → 合法预测和质量状态、观察和真值损失。低覆盖保留计划分母 | `round35-report.md`、`round35-evidence-manifest.json` |
| 缺失机制及权重 · 35–37 | 诊断用途。保持假设、重叠和敏感性限制 | `missing36.py`、`analyses36.py`、`transfer37.py` 输入合法时钟和人工应答机制 → MAR 权重/ESS、MNAR 条件敏感性、恢复统计。离线真值只供受控机制校验，不能推出真实总体排名 | `round36-report.md`、`round37-report.md` |
| 评分分层与发布资格 · 36–37 | 工程规则冻结。维护固定目标总体、覆盖和结论范围 | `qualification36.py`、`publication37.py` 核对共同合法点、分母、权重和条件 → 五种发布状态、目标与理由。阈值 12 个共同点且覆盖至少 30%，并须通过各状态的证据条件 | `round37-lock.json`、`round37-results.json`、 `test_publication37.py` |
| 特征有效性与分组时效 · 38–39 | 特征契约稳定保留；消融、扰动与共线性只诊断 | `features38.py`、`arrival39.py`、`design39.py`、`evaluation39.py` 输入人工带时点特征和反馈计划 → 不回填的预测、合法覆盖、共同评分和发布资格。依赖冻结拟合与第 37 轮状态机 | `round38-capability-inventory.json`、`round38-report.md`、`round39-report.md`、`round39-independent-review.md` |

## 冻结了的结论

- **第 34 轮**保留三个简单基线，原始自适应作为历史对照，稳健报警作为异常污染诊断。策略优势依人工机制而变。
- **第 37 轮**发布资格与最低覆盖独立于总体识别。支持的目标只能是预先绑定的计划总体或合法预测子群；MNAR 下只报告敏感性或证据不足。
- **第 39 轮**迟到特征可在真实到达后、未过期时前瞻使用；保留原时点的版本和决策。误差和弃权覆盖单列，组消融使用共同合法比较域。冗余系数不足以作单特征因果归因。

正式 Phase 3C-1、原 60 行留出、真实历史、Qlib、生产和交易不属于本清单的执行范围。

