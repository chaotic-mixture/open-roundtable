# 可复用开源组件调查

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
