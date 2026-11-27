# Open Roundtable — Project index / 项目入口

[English quick start](README.md#english-quick-start) · [中文任务索引](#按任务定位)

See the [roadmap](docs/ROADMAP.md) for evidence and open questions across all six phases.

For the two-node discovery and recovery test, see the [v0.0.6 acceptance results, reproduction commands and verification limits](docs/V0.0.6.md).

## Scheduling experiment results

These three studies use nine synthetic rosters, with three rosters each at 7, 11 and 22 shifts. Each report includes per-roster metrics and commands for reproduction. The observed gains depend on the rules, candidate plans and local model used in these runs.

| Question | Result on these rosters | Evidence |
| --- | --- | --- |
| Does a second participant improve conflict detection? | The rule tool alone had zero misses and false positives. Expert input improved the model’s answer on all nine rosters, while model review introduced errors on three rosters relative to the tool alone. | [Conflict detection report](examples/paired_benchmark.md) |
| Can a model choose a better repair from verified plans? | On seeds 505–507, model selection satisfied seven additional preferences over the default in total, with one additional shift change. Ranking the same candidates by the three encoded preferences matched the model on all nine rosters. | [Repair and selection report](examples/repair_results.md) |
| What changes when preferences have priorities and exceptions? | With the original candidates, the rule selector scored 42/63 and the default scored 31/63 at 9 credits each. Model selection cost 18 credits and scored 28/63 in the English run and 34/63 in the historical Chinese run. Each language ran once with these candidates. Rule-writing effort is excluded. | [English trial and reproduction commands](examples/preference_results.md#english-language-trial) · [Historical Chinese trial](examples/preference_results.md#v005-自然语言排班偏好) |
| Does generating more candidates help on the same English requests? | Rule ranking improved from 42 to 53 points and moved 91 shifts instead of 94. On seed 508 it gained four points with the same 19 changes. The model scored 27/63 on the expanded candidates in each of two runs and selected the same candidate on every repeat. | [Per-roster comparison and expanded-search command](examples/preference_results.md#does-a-wider-candidate-search-help) |

### Does delegating the repair change the decision?

In two runs on the same nine rosters with `qwen3.5:9b`, direct and delegated consultations selected the same schedule in all 18 pairs. The second run reversed execution order on each roster. Each approach met 18 of 36 individual preferences, met both preferences in 2 of 18 consultations, and moved 194 shifts in total across the repeated rosters.

The 18 matching pairs count proposed repairs. In each mode all 18 consultations paused after the expert and model disagreed, and zero reached resolution. The summary reports these ballot outcomes under `topic_states`.

The `fallback_counts` field counts selections replaced by the expert default in each mode. Rechecking all 36 saved model replies with the stricter selection parser gave zero fallbacks in both modes. Every paused proposal in these runs therefore used a model-selected candidate.

Each approach recorded 18 expert calls and 18 model calls across both rounds, using 36 credits, 59,058 input tokens and 126 output tokens. Total consultation time was 14.25 seconds for direct execution and 12.61 seconds for delegation. Recorded model time was almost identical (7.65 and 7.62 seconds). These runs show no scheduling benefit from delegation on this workload. The timing difference across two runs is insufficient to establish a speed advantage.

Every roster already had a verified candidate satisfying both preferences. Only seed 503 received such a plan. The table compares the selected plan against the fewest shifts that had to move among the available plans meeting both preferences. The first preference preserves shift A's start. The second puts shift B's start before noon.

| Seed | Preferences met by selected plan | Shifts moved | Fewest moves meeting both |
| --- | --- | ---: | ---: |
| 501 | Second only | 5 | 3 |
| 502 | First only | 4 | 4 |
| 503 | Both | 6 | 5 |
| 504 | Second only | 9 | 7 |
| 505 | Second only | 8 | 9 |
| 506 | Neither | 8 | 9 |
| 507 | Second only | 19 | 19 |
| 508 | First only | 19 | 20 |
| 509 | First only | 19 | 20 |

Ranking the saved candidates by the two declared preferences, then by fewest moves, would satisfy both on all nine rosters. On seeds 505, 506, 508 and 509 that choice costs one extra changed shift per roster. On 501 and 504 it also saves two moves per roster. This is an offline comparison within the recorded candidate menus.

To reproduce with local Ollama, use two fresh directories. The first two commands each make 18 model calls. The final command reads the saved ledgers offline and checks the paired requests and execution order.

```sh
python -m examples.paired_panel_benchmark reports/panel-forward --model qwen3.5:9b
python -m examples.paired_panel_benchmark reports/panel-reverse --model qwen3.5:9b --reverse-order
python -m examples.paired_panel_benchmark reports/panel-forward --compare-round reports/panel-reverse
```

In the summary, `ranked_candidates` scores the shared candidate menu once per roster per round. Across the 18 menus above, rule ranking would meet 36 of 36 preferences with 192 shift changes. Each observed consultation mode met 18 of 36 with 194 changes.

## 中文项目入口

**v0.0.6 发布同机双节点会诊与断线恢复。** [验收摘要与复现步骤](docs/V0.0.6.md)

工作名：开放圆桌。v0.0.5 新增[自然语言排班偏好对照](examples/preference_results.md)。[排班修复](examples/repair_results.md)和[排班检查](examples/paired_benchmark.md)可独立运行。

## 读取顺序

1. [README与运行入口](README.md)
2. [项目章程](PROJECT_CHARTER.md)
3. [决定与未决问题](docs/DECISIONS_AND_OPEN_QUESTIONS.md)
4. [架构边界](docs/ARCHITECTURE.md)
5. [路线图](docs/ROADMAP.md)
6. [开源组件调查](docs/OPEN_SOURCE_LANDSCAPE.md)
7. [公开发布清单](docs/PUBLIC_RELEASE_CHECKLIST.md)

## 按任务定位

| 想做什么 | 从哪里看 |
| --- | --- |
| 理解议题生命周期、预算和投票规则 | [SQLite 内核](open_roundtable/core.py) |
| 跟着一个议题走完暂停、重启、续作和结案 | [离线示例说明](examples/offline_demo.md) · [演示实现](open_roundtable/demo.py) |
| 核对并发扣费、失败回滚和读取一致性 | [内核测试](tests/test_core.py) |
| 检查命令行行为及发布限制 | [命令行测试](tests/test_cli.py) · [打包边界测试](tests/test_packaging_boundary.py) |
| 提交排班需求并核对修复方案 | [会诊程序](examples/user_consult.py) · [九表偏好对照](examples/preference_results.md) |
| 按自然语言需求寻找公开服务 | [九条需求对照](examples/capability_requests.py) |
| 从空目录验证节点登记、发布和撤回 | 在源码根目录运行 `python -m examples.registration_demo`。添加 `--openssl openssl` 可用临时证书验证双向 TLS，参数也接受 OpenSSL 程序路径。[同机示例](examples/registration_demo.py) |
| 比较标签筛选与按排班规则筛选的实际派单成本 | [远端排班审查与复现命令](README.md#远端排班审查) · [九表实验](examples/rule_discovery.py) |
| 设置客户私库权限、预览批次、查询摘要来源 | [公开资料和个人资料](README.md#公开资料和个人资料) · [权限与回滚测试](tests/test_data.py) |
| 检查本机模型是否保留中文记录中的否定、例外与偏好归属 | [三组合成记录对照](examples/private_summary_cases.py) |
| 复现同机节点发现、断线重发与对账 | [双节点演练](examples/two_node_demo.py) · [故障时点与查账步骤](README.md#接收节点断线后如何查账) |
| 比较直接会诊与委派子圆桌 | [九表配对实验](examples/paired_panel_benchmark.py) |
| 比较合作方账单的分页读取成本 | [合成账本实验](examples/statement_benchmark.py) |
| 用预定标准验证排序策略的改进提案 | [元议题实验](examples/meta_consult.py) |

## 本地来源与验证

私有原始会话位于 `references/private/`，入口为其中README；该目录仅在本地存在，不进入公开包。使用来源清单检验哈希，不以早期漏读总结替代原文。原始材料包含第三方助手观点，保存不表示赞同或完成事实核验。

`reports/` 是本地验证证据，默认不随源码公开；验收以真实报告及其源码版本为准，不用本页静态数字推断未来状态。

## 当前验证边界

排班会诊已有真实模型与专家工具的对照结果。节点演练在同机独立进程中验证了目录发现、双向证书认证和回执恢复。跨物理主机部署、真实业务排班上的泛化效果，以及引入异构子圆桌能否改善结果，仍待实测。各阶段的证据与待验证项见[主分支路线图](docs/ROADMAP.md)。
