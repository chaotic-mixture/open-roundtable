# Open Roundtable — 项目入口

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
