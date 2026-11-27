# Open-source design references / 可复用开源组件调查

## EigenFlux and capability discovery

EigenFlux lets agents publish capabilities and needs, then find relevant peers through search, subscriptions and recommendations. For a roundtable convener, the relevant question is whether a provider can handle every rule in a request before receiving the roster. [EigenFlux discovery guide](https://github.com/phronesis-io/eigenflux/blob/main/docs/dev/discovery.md) · [Official FAQ](https://www.eigenflux.ai/blog/eigenflux-faq)

Open Roundtable has a local comparison for that question. From the repository root, run `python -m examples.rule_discovery --dispatch`. On nine synthetic rosters, selecting by tag led to 36 dispatch attempts. The provider declarations ruled out 21 before roster transmission. Checking all requested rules during discovery reduced attempts to 15 and returned the same 15 correct receipts. This measures the effect of structured matching on the local directory. Evaluating recommendations based on natural-language needs would require a separate comparison on the same labeled requests. [Local experiment](../examples/rule_discovery.py)

### Matching English requests to published offers

Run `python -m examples.capability_comparison` with local Ollama serving `qwen3.5:9b`. Three English requests share a catalog of two schedule repair services and one audit service. Both repair services should match the repair request, only the audit service should match the read-only request, and none should match payroll.

| Selector | Missed offers | Incorrect offers | Model calls | Input / output tokens |
| --- | ---: | ---: | ---: | ---: |
| Exact tag | 1 | 0 | 0 | 0 / 0 |
| Model over all offers | 1 | 2 | 3 | 405 / 26 |
| Operator's allowed tags | 0 | 0 | 0 | 0 / 0 |
| Model over allowed tags | 0 | 0 | 2 | 194 / 17 |

The unrestricted model found the synonymous repair service missed by exact matching. On the read-only request it selected both editing services and missed the auditor. The operator's repair rule explicitly included both repair tags, and the audit rule allowed only the audit tag. Model review added no correct selections to those rules in this run. An empty payroll menu skipped inference. This experiment measures local recommendations on three labeled requests. Provider execution and EigenFlux matching were outside its scope.

The table records the original prompt at commit `cec7400`. A follow-up prompt made the read-only constraint explicit. It found the auditor but missed one repair provider. The current prompt also asks the model to evaluate every offer independently, including equivalent services with different tags. On one rerun with the same three requests it returned both repair providers, only the auditor for the read-only request, and no payroll provider. Unrestricted selection used 3 calls and 534 input / 23 output tokens. Restricting tags first used 2 calls and 280 input / 17 output tokens. The tag rules alone still returned the same correct sets. These requests were used to tune the prompt, so this result measures fit to the examples.

### When two providers share a tag

Run `python -m examples.capability_comparison --held-out` for four additional English requests scored against labels set before inference. The prompt from commit `e767f97` was held fixed. Two auditors cover different rules, and two repair providers resolve room clashes by different means. Each selected provider must meet the entire request independently.

| Method | Missed offers | Incorrect offers | Calls | Input / output tokens |
| --- | ---: | ---: | ---: | ---: |
| Exact tag | 1 | 5 | 0 | 0 / 0 |
| Allowed tags | 0 | 5 | 0 | 0 / 0 |
| Model over all offers | 0 | 1 | 4 | 876 / 28 |
| Model over allowed tags | 0 | 2 | 4 | 645 / 31 |

In one local run with `qwen3.5:9b`, both model methods selected exactly the right provider for the rest audit, room audit and fixed-start repair. When asked for one report covering both audit rules, no individual offer qualified. The unrestricted model recommended one partial match and the filtered model recommended both. Reading descriptions helped disambiguate shared tags in these cases. A compound request still needs a check that each selected offer covers every required rule. This run measured recommendations only.

## 中文调查记录

调查日期：2026-11-10。范围：Agent通信、临时多方协作、能力封装。仅原始仓库/规范只读核验，不以共享会话里的搜索结论作为事实。未安装候选、未跑候选测试、未接外部服务。

## 能力问题与检索边界

关键词：agent interoperability、group chat orchestration、MCP tools、recursive teams、budget persistence、outcome feedback。消息/任务协议可复用；本项目原始的议会政策、贡献结算、实践反馈与递归关系不能被外部框架默认值替换。

这不是全网穷举，也不构成“没有同类/蓝海”的证明。本轮初筛两个明确候选和一个协议参考；暂不复述未经重新核实的第三方项目清单。

## 候选

| 候选 | L1原始来源事实 | 许可 | 采用判断 |
|---|---|---|---|
| A2A | README声明跨框架能力发现、长任务协作、不暴露内部状态、同步/流式/异步；有多语言SDK入口 | 仓库LICENSE核验为Apache-2.0，SDK各自许可/版本未逐个核验 | **隔离改造候选**：Phase 2评估传输适配，不拿它当共识或经济机制；目前不依赖 |
| Microsoft AutoGen | README目前明确标为maintenance mode，支持人机/多Agent与AgentTool/MCP例子；建议新用户参考其后继框架 | LICENSE-CODE为MIT；LICENSE为CC BY 4.0，代码和文档许可不同 | **只作参考**：学习编排接口，避免在Phase 0引入大型运行时与模型绑定；不复制代码或文档 |
| MCP架构规范 | 指定版本描述host/client/server、能力协商、资源/工具/提示接口；host处理权限边界 | 本轮未核验规范再分发许可，只有链接与简述 | **只作参考**：后续专业工具适配；不把MCP视为全网共识、身份匿名或结算协议 |

## 原始来源

- A2A README：https://raw.githubusercontent.com/a2aproject/A2A/main/README.md
- A2A LICENSE：https://raw.githubusercontent.com/a2aproject/A2A/main/LICENSE
- A2A CONTRIBUTING：https://raw.githubusercontent.com/a2aproject/A2A/main/CONTRIBUTING.md
- AutoGen README：https://raw.githubusercontent.com/microsoft/autogen/main/README.md
- AutoGen code license：https://raw.githubusercontent.com/microsoft/autogen/main/LICENSE-CODE
- AutoGen documentation license：https://raw.githubusercontent.com/microsoft/autogen/main/LICENSE
- MCP固定版本架构：https://modelcontextprotocol.io/specification/2025-11-25/architecture

以上为调查时读取的页面事实，不声称固定版本是最新版本，不声称main分支内容永久不变。首次尝试AutoGen LICENSE-DOC路径返回404，随后根据README改读真正的LICENSE，未以猜测许可补位。

## 维护、依赖与测试边界

A2A的CONTRIBUTING包含规范/文档开发与lint说明；本轮没有运行其CI或SDK用例，也没有核查全部提交历史。AutoGen维护模式来自当前README，不从stars推断可靠性。AutoGen示例涉及外部模型账户；不因此允许本项目调用任何账户。

## 当前采用决定

Phase 0 直接使用Python标准库sqlite3/unittest完成机械状态实验，无上述第三方运行时依赖。不是另造完整A2A/MCP，也不是断言现成框架不可用。后续适配必须先在临时目录PoC：固定输入、失败行为、消息恢复、预算与真实消耗记录、权限与许可证检查。Phase 1真实任务选择前重新审阅候选。

## 复查条件

开始跨终端联调、接真实工具/模型、打包发布或更换运行时前复查版本、维护模式、许可、测试和适配成本。当前项目没有任何候选“已集成”能力。

## EigenFlux 与议员发现（2026-11-23）

EigenFlux 把代理发布的内容分为信息、需求和可提供的能力，并按代理表达的兴趣匹配广播。对开放圆桌，最直接的用途是让发起方按能力找到候选议员。官方资料描述了共享 Hub 的匹配机制；独立目录之间的发现、撤回传播与信任仍需单独验证。[官方仓库](https://github.com/phronesis-io/eigenflux) · [官方说明](https://www.eigenflux.ai/blog/eigenflux-faq)

建议先做本地可复现的目录实验：节点从公开库发布经审核的能力描述，消费方用脚本按任务需求和预算筛选，选定后沿用现有授权、会诊和回执流程。对同一批合成需求比较按节点名手选、精确标签筛选和自然语言匹配，统计漏选、误选、额外模型调用及最终任务成功率。新增供给、能力撤回、额度耗尽和私库含有相似关键词应分别设用例。此项为实验设计，相关收益尚待实测。

上游主仓库将许可证表述为附加商标条件的 Apache 2.0 修改版。引用设计与来源可沿用本项目的文献记录方式；若后续引入具体实现，须按实际组件核对许可证、归属和品牌使用范围。[上游许可证](https://github.com/phronesis-io/eigenflux/blob/main/LICENSE)
