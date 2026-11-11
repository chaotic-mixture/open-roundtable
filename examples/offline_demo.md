# 离线合成圆桌示例

在项目根目录直接运行，无须 pip install，也不会联网：

```bash
python -m unittest discover -s tests -v
python -m open_roundtable demo --db examples/my-new-demo.sqlite3
```

`--db` 必须是**尚不存在的文件**，父目录必须已存在。重复运行同一路径会明确拒绝；空文件和非 SQLite 文件同样不覆盖。要再次演示，换一个新文件名。

CLI 输出 JSON，声明 `synthetic preset demonstration; no real models; no external API calls`。四种议员均为预置数据，不是模型、专家或人类的真实回答。`roundtable` 的 `source` 仅作来源引用，不执行嵌套调用。

此示例实际执行：创建 SQLite 议题 → 四种成员入册 → 开始并冻结名册 → 发言耗尽预算暂停 → 关闭并重开数据库 → 补充本地测试 credits → 续作 → 四人名册中三人同意 → RESOLVED → 附加合成反馈和特征标签。另存一个 `DRAFT / meta / lowest` 元议题，不执行代码变更。

完整示例实现位于 `open_roundtable/demo.py`；它与 CLI 使用同一内核。每次成功输出的随机 topic ID 与数据库内容对应，不能拿示例 JSON 当真实 AI 推理证据。

## Python API 的最小用法

从项目根目录启动 Python，使用专用数据库路径：

```python
from open_roundtable import Kernel

kernel = Kernel('examples/my-api-state.sqlite3')
try:
    topic = kernel.create('Synthetic question', credits=1)
    kernel.join(topic, 'synthetic-human', kind='human', source='synthetic:fixture')
    kernel.start(topic)
    assert kernel.post(topic, 'synthetic-human', 'Synthetic preset message', cost=1)
    assert kernel.get(topic)['state'] == 'PAUSED'
    kernel.top_up(topic, 2)
    kernel.vote(topic, 'synthetic-human', True)
    kernel.feedback(topic, 'synthetic-human', 'Synthetic practice note', source='synthetic:trial')
    kernel.tag(topic, 'synthetic-human', 'synthetic')
    print(kernel.get(topic))
finally:
    kernel.close()
```

与 CLI 不同，`Kernel(path)` 明确支持重开现有内核数据库；`create` 总是产生新 ID。不要传入其他应用数据库。当前不提供数据库迁移、身份认证、远程接入、资源市场或最终经济政策。

本地测试 credits 不是 token、货币或贡献报酬。只有 `post` 按调用者指定的正整数 `cost` 扣减；投票、元数据和反馈不扣费。已解决议题不能再发言、投票、改名册或补充预算，但可继续附加反馈和特征标签。
