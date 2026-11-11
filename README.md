# Open Roundtable / 开放圆桌

**An experimental, composable roundtable runtime for heterogeneous participants.**

开放圆桌探索一个由模型、专家系统、人类和其他圆桌共同参与的协作环境：遇到难题时临时组织会诊，在有限资源下讨论与实践反馈，并逐步支持跨节点供给、历史重访和圆桌递归组合。

**当前是 Phase 0 离线参考内核，不是已经上线的专家网络。** 工作名未做商标或公开包名占用确认。

## 从这里开始

- [项目章程](PROJECT_CHARTER.md)：原始目标与本轮范围。
- [项目入口](PROJECT_INDEX.md)：文档导航。
- [架构](docs/ARCHITECTURE.md)与[路线图](docs/ROADMAP.md)。
- [待定问题](docs/DECISIONS_AND_OPEN_QUESTIONS.md)：投票、预算、递归与经济机制仍可调整。
- [开源组件调查](docs/OPEN_SOURCE_LANDSCAPE.md)：候选协议与采用边界。

## 本地运行

代码从仓库根目录直接运行（Python 3.11+），只使用 Python 标准库；运行时无第三方依赖，不需要 API key。目前构建后端明确拒绝 wheel/sdist，暂不支持 pip 安装或发布包。

```bash
python -m unittest discover -s tests -v
python -m open_roundtable --help
python -m open_roundtable demo --db demo.sqlite3
```

示例数据库应为**尚不存在的新路径**。演示不允许静默覆盖已有数据库；再次运行应换一个新文件名。不要用真实业务数据库运行示例。

演示使用明确标为 **synthetic / no real models** 的预置成员与消息，目的只是检查持久化、预算和程序状态，不证明真实模型产生共识或协作提升。

## 排班实验

仓库提供固定随机种子的合成排班表、计分规则和独立评分程序。运行 `python -m examples.generate_overnight` 可重建九张跨天排班表，与 [实验输入](examples/overnight_inputs.txt) 对照。成员调用由实验主持者在内核之外派发。SQLite 运行记录默认保存在本地。

## Phase 0 验证对象

- 本地SQLite中的议题、异构成员类型、消息与预算状态。
- 预算不足时暂停，补充测试额度后续作，重启后状态仍可读取。
- 固定名册的严格过半同意规则；重复成员投票不能增加票数。
- 反馈和特征标签记录。
- 原始会话、用户定义和助手建议分开保存；私有来源不进入源码发行。

`roundtable` 成员类型不等于递归调用已经实现；`human`/`expert` 类型不代表身份或资质认证。程序状态 `RESOLVED` 不等于结论正确，也不会自动执行任何外部操作。

## 没有实现的部分

真实LLM/专家工具适配、公开发现、跨终端通信、匿名机制、自动工具执行、活圆桌嵌套、真实贡献计价/信誉结算、订阅共享、区块链以及后台自修改。它们保留在路线图中，没有被原型的简化设计取消。

本地整数 credits 是测试预算单位，不是跨模型 token 等价、可兑现余额或货币。原始的贡献回馈机制尚未写死为一个公式。

## 运行与发布边界

不监听网络、不发起付费调用、不读取账户凭据、不把订阅默认当可转售资源。安全限制见 [SECURITY.md](SECURITY.md)。

本地原始参考存于 `references/private/`，不随仓库公开；第三方助手的历史主张未经逐条核实。`reports/` 保存本地真实验收输出，也默认不公开。正式发布使用[白名单与独立审查](docs/PUBLIC_RELEASE_CHECKLIST.md)，不能直接压缩整个工作目录。

## License

Original code and original project documentation: MIT. Third-party materials, model/service rights and private conversation exports are not relicensed; see [NOTICE.md](NOTICE.md).
