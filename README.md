# Open Roundtable / 开放圆桌

**An experimental, composable roundtable runtime for heterogeneous participants.**

开放圆桌探索一个由模型、专家系统、人类和其他圆桌共同参与的协作环境：遇到难题时临时组织会诊，在有限资源下讨论与实践反馈，并逐步支持跨节点供给、历史重访和圆桌递归组合。

**v0.0.6 发布同机双节点会诊与断线恢复。** [验收摘要与复现步骤](docs/V0.0.6.md)

**v0.0.5 在已验证的排班候选上比较带优先级、冲突和例外的自然语言需求。** [逐表结果、评分方法与复现命令](examples/preference_results.md) · [排班修复实验](examples/repair_results.md) · [排班检查实验](examples/paired_benchmark.md)

## 从这里开始

- [项目章程](PROJECT_CHARTER.md)：原始目标与本轮范围。
- [项目入口](PROJECT_INDEX.md)：文档导航。
- [架构](docs/ARCHITECTURE.md)与[路线图](docs/ROADMAP.md)。
- [待定问题](docs/DECISIONS_AND_OPEN_QUESTIONS.md)：投票、预算、递归与经济机制仍可调整。
- [开源组件调查](docs/OPEN_SOURCE_LANDSCAPE.md)：候选协议与采用边界。

## 本地运行

### Phase 2 双节点断线恢复实验

准备本机 Ollama 和 qwen3.5:9b 后，从项目根目录执行：

```bash
python -m examples.two_node_demo run reports/two-node-run
python -m examples.two_node_demo verify reports/two-node-run
```

第一条命令需要新的输出目录。A、B 在同一台机器的不同进程和端口运行，各自持有公开库、私库和传输账本。两端从同一个引导目录取得注册信息，再读取对方的公开目录确认地址。A 显式共享合成议题，B 检查来源，调用规则专家和本机模型，将结果持久化。故障注入在 B 落账后、发送 HTTP 回执前终止进程。B 在原端口重启后，A 重发同一内容摘要标识的请求，B 返回缓存。A 镜像 B 的消耗，两个账本所记的 2 credits 属于同一次会诊。

第二条命令离线核对收发记录、模型回执、表决结果、扣费和两端私库中植入的随机标记。原始传输记录只在本地保存。测试覆盖回执丢失窗口。若接收方在模型执行期间中断且没有完整回执，会保留待核对任务，避免自动重复推理。该演练使用受信任的本机回环网络；跨物理主机的认证、加密和部署仍待验收。可通过 ROUNDTABLE_OLLAMA_PORT 指定本机模型端口。

恢复发送端积压的议题可运行 `python -m examples.peer_node reports/release-peer-run/A resume`。命令从本地账本查找本节点发出且缺少回执的请求，逐笔重试，重新检查原内容仍在共享范围内。已撤回或更改的议题保留待处理；任一请求失败会返回非零退出码，其他请求继续处理。全部完成后再次运行输出空对象。

### 公开资料和个人资料

每位客户使用独立目录。以下命令初始化两个 SQLite 文件，公开库保存带 HTTPS 来源的资料，私有库保存该客户的个人记录。运行账本另行保存。

```bash
python -m open_roundtable.data runtime/customer-a init
python -m open_roundtable.data runtime/customer-a import private personal.json
python -m open_roundtable.data runtime/customer-a maintain-private profile
python -m open_roundtable.data runtime/customer-a public-feed
```

导入文件是 UTF-8 JSON 数组，例如 `[{"source":"profile","body":"偏好上午开会"}]`。默认 sealed 模式允许写入和无网络的本地文本整理，结果只写回私库。选择 `policy local --model qwen3.5:9b` 后可用本机 Ollama 摘要；选择 `policy api --model MODEL --endpoint HTTPS_URL` 后，维护命令会将该条正文发给指定的兼容聊天接口。可通过环境变量 `ROUNDTABLE_API_KEY` 配置密钥，通过 `policy sealed` 撤销后续调用。已发送的数据由接收方处理。

公开库支持 `fetch-public URL` 采集指定的 UTF-8 文本、`import public FILE` 批量导入、`import-feed URL` 拉取选定节点的 JSON 目录。资料默认仅本机使用。`share-public URL` 允许该条进入 `public-feed`，加 `--off` 可撤下。内容变更后需要重新选择共享。节点运营者自行托管导出的目录；来源字段由提供方声明。

### 提交自己的排班和需求

在 [JSON 示例](examples/consult_input.json) 中填写议题、自然语言偏好和班次。标识可使用中文，起止时间是本周起点后的整数小时。可选的 `criteria` 按优先级列出每个班次的闭区间条件，支持绝对开始、结束、当地小时、日期和相对原开工时间的偏移。程序据此搜索更多可行方案并报告逐项满足情况；只有自然语言时由模型选择，独立验证硬约束。

```bash
python -m open_roundtable consult examples/consult_input.json --db my-consult.sqlite3
python -m examples.preference_benchmark reports/my-preferences --expand
python -m open_roundtable verify-batch reports/my-preferences
```

第一条命令使用新的数据库文件。后两条用于固定九表实验。保持模型、代码及搜索参数一致，重复批次命令可在原目录续跑。有效返回按次扣费；连接失败交付默认方案并登记待重试。账本保留调用意图、原始请求、回复、耗时和失败记录。恢复时使用已经持久化的回复。若进程在远端执行后、回复落盘前被终止，前一次调用标为 unknown，其实际 tokens 无法从本地恢复。CSV 的 calls 包含重试，credits 按成功记入的成员回复累计。verify-batch 支持历史 v0.0.5 和新格式，缺行或关键记录不一致时退出失败。

新议题的表决绑定到方案版本。对仍在讨论或暂停的议题，`python -m open_roundtable propose DB TOPIC 当前版本 "修订方案"` 开启下一版。成员通过 `python -m open_roundtable vote DB TOPIC 新版本 MEMBER yes` 表决。旧版选票可在 prior_votes 中查看，原始议题对应版本 0。

对提供 criteria 的输入，top_priority_search 记录最高优先项的可行见证。若启发式扩展仍未找到见证，再枚举合法周工时；search_nodes（默认 10000）控制枚举节点上限。只有穷尽搜索空间才报告 infeasible，到达上限报告 unknown。模型选到满足较少已声明条件的方案时，专家可以投反对票，议题保留以供修订。

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

内核与数据管理命令均由用户在本机启动。客户显式选择 API 维护后，指定服务可按其计费规则收费。权限与部署边界见 [SECURITY.md](SECURITY.md)。

本地原始参考存于 `references/private/`，不随仓库公开；第三方助手的历史主张未经逐条核实。`reports/` 保存本地真实验收输出，也默认不公开。正式发布使用[白名单与独立审查](docs/PUBLIC_RELEASE_CHECKLIST.md)，不能直接压缩整个工作目录。

## License

Original code and original project documentation: MIT. Third-party materials, model/service rights and private conversation exports are not relicensed; see [NOTICE.md](NOTICE.md).

### 按本地标记重访会诊

运行双节点示例后，用输出中的 request_id 替换 JOB_ID。下列命令为 A 端已经收到回执的会诊添加标记，再按标记查阅完整回执。B 端可独立标记同一议题。标记存放在各自的私有库中，由本机操作者管理。查询使用本地账本，可离线执行，重复标记同一会诊只保留一条。

~~~
python -m examples.peer_node reports/release-peer-run/A mark JOB_ID weekend
python -m examples.peer_node reports/release-peer-run/A revisit weekend
~~~

接收方须在本机运行 `python -m examples.peer_node NODE_DIR permission PEER allow`，才能受理该节点的会诊。授权绑定已发现的名称和地址，节点地址变化后需要重新授权。用 `revoke` 代替 `allow` 可撤销后续受理和回执重发；已经受理的任务可以继续。旧节点库首次使用此版本时也需要授权。示例自动在 B 授权 A。此控制适用于可信回环环境，发送方名称和地址尚无密码学身份证明。

双节点演练支持第二个中断位置。运行 `python -m examples.two_node_demo run reports/peer-before-receipt --fault before-receipt`，接收节点会在会诊库保存完整回执之后、网络账本登记费用之前退出。重发时先核对权限和公开议题，再从会诊库恢复回执和费用。可用 `python -m examples.two_node_demo verify reports/peer-before-receipt` 离线对账。缺少完整回执的执行仍需本地核对。

### Phase 3 本地子圆桌实验

在源码根目录运行 `python -m examples.nested_consult nested.sqlite3 AXR:20-26 BXR:24-29`，数据库文件须为新路径。父议题从 4 个测试 credits 中划给子圆桌 3 个。子圆桌执行排班检查并表决，花费 1 个，将已通过的提案返回父议题，退回余下 2 个。父议题再花 1 个独立核验结果，最终余额为 2。父级账本中子圆桌发言的费用包含下级消耗，统计总费用时只汇总同一层。此示例检验本地委派、结算与表决；递归对复杂任务完成质量的收益仍待对照实验。

运行 `python -m examples.nested_benchmark reports/nested-paired` 可复现直接执行和子圆桌执行的对照。两组使用相同的区间检查器、逐小时复核器和 4 credits 预算。固定九张表各含 7、11 或 22 个班次，每种规模三张；每张表每组运行一次。两组各完成 9/9，漏报与误报均为零，各用 18 credits。本次本机端到端耗时中位数分别为 126.84 ms 和 152.13 ms，包含建库和持久化。对这组可直接脚本化的任务，加一层委派没有提高准确率。结果目录保存逐表 CSV 和可独立回读的 SQLite 账本。
