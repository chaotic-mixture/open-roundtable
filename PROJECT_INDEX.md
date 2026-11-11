# Open Roundtable — 项目入口

工作名：开放圆桌。v0.0.2 提供 SQLite 内核、本地模型与排班专家会诊、离线复核和固定样例。验证方法见 README。

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

## 本地来源与验证

私有原始会话位于 `references/private/`，入口为其中README；该目录仅在本地存在，不进入公开包。使用来源清单检验哈希，不以早期漏读总结替代原文。原始材料包含第三方助手观点，保存不表示赞同或完成事实核验。

`reports/` 是本地验证证据，默认不随源码公开；验收以真实报告及其源码版本为准，不用本页静态数字推断未来状态。

## 当前下一步

完成并验收离线内核之后，选一个有独立验证条件的低风险真实任务，讨论成员适配和会诊策略。现阶段不启动全网服务、结算或自改进后台。
