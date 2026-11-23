<!-- 发布文档副本：已适配外部解释器路径和本地证据引用。 -->

# quant-lab 第 41 轮轻量合成研发签收

本次签收范围为第 24–39 轮能力、第 40 轮冻结候选及本地轻量合成运行。完成日为 2026-11-23（香港时间）。

## 最终状态

| 字段 | 判定 |
| --- | --- |
| `lightweight_research_phase` | `completed` |
| `lightweight_release_accepted` | `yes` |
| `remaining_lightweight_P0_P1` | `0` |
| `phase42_required` | `no` |

签收人为本地工程验收执行者。独立审查者核验运行记录、四类本地证据、冻结完整性及维护范围。签收文档和证据摘要用于本地交付。独立校验器起初将烟测的所有直接依赖都查在第 40 轮外层清单，后改为核对外层清单及其已锚定的源码清单。两处来源摘要均匹配，修正过程另行保留。

## 冻结版本与验收

- 第 40 轮候选清单 SHA-256 为 `052a66eaab3f08cbe2ea61ac8907a9cb82e8be8c2af96938e46f618ee323f394`，所列 **418 项**逐文件匹配。源码冻结清单 SHA-256 为 `3995b3dbcdbe403a4a42bb7248c82d2a36469de35b3b73c922278ce39a5693ae`，其中 **102 项**源文件保持原字节。冻结策略、统计发布规则和第 29 轮审计入口均匹配。
- 能力以[冻结清单](round40-capabilities.md)为索引。维护范围含独立入口、样本时序与完整性、三个核心预测基线、合成发布资格、特征到账及证据回执；历史自适应、Bootstrap、缺失机制和特征群组诊断遵守原有定位。
- 在冻结的 Windows CPython 3.10.11 上，仅运行一次 完整轻量回归（原始本地证据）。实际标志为 isolated=1、no-site=1、禁止字节码写入=1。320/320 通过，0 失败、0 跳过，退出码 0，外部耗时 14.500 秒。测试身份与第 39 轮相同，回归前后的来源摘要一致。原始 stdout 和 stderr 在回执内。

## 最终端到端结果

只运行一次固定探针，重放第 39 轮开发种子 69100 的 [三个既有样例](round41-smoke-result.json)。

| 样例 | 共同评分点 | 发布状态及其范围 |
| --- | ---: | --- |
| timing / short | 120/120 | `population_supported_under_assumptions`，本合成计划总体 |
| timing_mnar / short | 55/120 | `sensitivity_only`，同一已验证预测群体的敏感性分析 |
| timing_mnar / short_abstain | 27/120 | `insufficient_evidence`，低于覆盖门槛 |

55/120 达到最低覆盖条件，但已知 MNAR 机制不满足总体识别假设，因此保持敏感性。27/120 虽超过 12 点，覆盖率 22.5% 低于 30%，因此拒绝升级。每次探针的四类本地文件经过摘要核验，回执的 `origin_authenticated=false`。

## P0/P1、限制与维护

轻量交付无待修 P0 或 P1。原有 P2 限制按[技术债清单](round40-closeout.md)保留，主要是跨轮数据形状、同权限的本地证据存储和条件统计解释。本轮未改源码、策略、测试名单或研究规则。本阶段在本轮结束，维护按冻结版本处理可复现缺陷。

从已交付的轻量目录复现，详细环境与摘要验证见[统一维护指南](round40-guide.md)。

```powershell
$syntheticPython = (Get-Command python.exe).Source
& $syntheticPython -I -S -B .\run_synthetic.py
& $syntheticPython -I -S -B .\smoke40.py
```

日常复跑仍限合成环境。原始 60 行留出本轮未评分，历史首次使用状态未知，三个留出就绪状态均为 `no`。正式 22 项为 `not_executed`，正式 Phase 3C-1 Gate 维持原判定。真实历史、Qlib、生产与交易继续 No-Go，正式研究准入为 `insufficient_evidence`。本轮未修改生产工作区、推送或触发 GitHub Actions。

最终材料互相绑定：候选输入核验（原始本地证据）、独立复核（原始本地证据）、[机器签收记录](round41-acceptance.json)、最终 SHA-256 清单（原始本地证据）。
