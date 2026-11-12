# Open Roundtable / 开放圆桌

**An experimental, composable roundtable runtime for heterogeneous participants.**

开放圆桌探索一个由模型、专家系统、人类和其他圆桌共同参与的协作环境：遇到难题时临时组织会诊，在有限资源下讨论与实践反馈，并逐步支持跨节点供给、历史重访和圆桌递归组合。

**v0.0.5 在已验证的排班候选上比较带优先级、冲突和例外的自然语言需求。** [逐表结果、评分方法与复现命令](examples/preference_results.md) · [排班修复实验](examples/repair_results.md) · [排班检查实验](examples/paired_benchmark.md)

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

## Phase 1 排班会诊实验

### 可复现的离线示例

以下示例使用固定种子 47 和标为 synthetic 的预置回复，无需 Ollama。预期结果为 7 项违规、0 漏报、0 误报、两票同意、扣除 2 credits，状态为 `RESOLVED`。运行标识和耗时每次会变化。请选择新的输出目录。

```bash
python -m examples.synthetic_consult reports/repro-47
python -m examples.auto_consult verify reports/repro-47
```

### 自动运行一次本机会诊

启动本机 Ollama，准备好支持 JSON 回复的模型（默认 `qwen3.5:9b`）。从仓库根目录执行以下命令。第一次命令需要新的输出目录，第二次可以在模型服务关闭后重复执行。

```bash
python -m examples.auto_consult run reports/my-consult --seed 47
python -m examples.auto_consult verify reports/my-consult
```

程序生成七个班次，请区间规则专家提案，再把排班和提案提交到本机 Ollama 的 11434 端口。模型返回最终清单、理由和表决；专家根据最终清单与自身结果是否一致投票。两票同意时内核结案。独立逐小时评分器将标准答案、漏报和误报写入反馈。一次专家返回和一次模型返回分别扣 1 credit。

输出目录保存模型及其摘要、输入、时间、源码指纹、完整模型请求与原始响应，以及 SQLite 消息、投票、余额和评分。模型返回的 token 计数与内核 credits 分开记录。失败时已写入的材料保留在该目录，再次运行使用新目录。

### 自动比较三种方式

```bash
python -m examples.benchmark_consult reports/paired-reproduction --model qwen3.5:9b --first-seed 501 --repetitions 3
```

每档三张表，全程使用本机 Ollama。逐组指标写入输出目录的 `results.csv`。参考运行中，专家提案改善了九张表的模型答案；单工具已逐张正确，模型复核在三张表上引入了错误。

### 手动记录外部模型对照

在仓库根目录运行 `python -m examples.phase1_consult consult.sqlite3 init`，使用新的数据库文件名。程序为单模型、单工具和圆桌建立三个议题，分别调用单工具组和圆桌组的排班专家，并打印圆桌专家意见。专家按区间检测人员、房间重叠并合并连续工时。

向外部模型提供 `examples.phase1_consult.CASES` 中的三张表和 [四项规则](examples/overnight_inputs.txt) 的说明。先记录独立答案，再把圆桌专家意见交给同一个模型复核。两份文本均按每表一行的格式填写：表号、人员重叠、房间重叠、休息不足、连续超时，以竖线分隔，空集填 `-`。

```bash
python -m examples.phase1_consult consult.sqlite3 record model independent.txt
python -m examples.phase1_consult consult.sqlite3 record roundtable reviewed.txt
python -m examples.phase1_consult consult.sqlite3 score
```

每条成功返回扣一个本地 credit。圆桌复用单模型答案时，完整会诊成本包含这次共享调用。评分器逐小时计算标准答案，按表和规则报告差集。

## Phase 0 验证对象

- 本地SQLite中的议题、异构成员类型、消息与预算状态。
- 预算不足时暂停，补充测试额度后续作，重启后状态仍可读取。
- 固定名册的严格过半同意规则；重复成员投票不能增加票数。
- 反馈和特征标签记录。
- 原始会话、用户定义和助手建议分开保存；私有来源不进入源码发行。

`roundtable` 成员类型不等于递归调用已经实现；`human`/`expert` 类型不代表身份或资质认证。程序状态 `RESOLVED` 不等于结论正确，也不会自动执行任何外部操作。

## 没有实现的部分

路线图仍包括通用模型和专家插件接口、公开发现、跨终端通信、匿名机制、活圆桌嵌套及贡献结算。

本地整数 credits 是测试预算单位，不是跨模型 token 等价、可兑现余额或货币。原始的贡献回馈机制尚未写死为一个公式。

## 运行与发布边界

不监听网络、不发起付费调用、不读取账户凭据、不把订阅默认当可转售资源。安全限制见 [SECURITY.md](SECURITY.md)。

本地原始参考存于 `references/private/`，不随仓库公开；第三方助手的历史主张未经逐条核实。`reports/` 保存本地真实验收输出，也默认不公开。正式发布使用[白名单与独立审查](docs/PUBLIC_RELEASE_CHECKLIST.md)，不能直接压缩整个工作目录。

## License

Original code and original project documentation: MIT. Third-party materials, model/service rights and private conversation exports are not relicensed; see [NOTICE.md](NOTICE.md).
