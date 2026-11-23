# 公开发布边界

v0.0.6 以 Git 源码快照发行。wheel 和 sdist 构建由后端禁用。

## 可公开候选（仍须审查）

- LICENSE、NOTICE.md、README.md、PROJECT_CHARTER.md、PROJECT_INDEX.md、CONTRIBUTING.md、SECURITY.md、.gitignore
- pyproject.toml、MANIFEST.in（若存在且已审核）
- open_roundtable/*.py
- tests/*.py
- examples/ 中的原创实验程序、合成排班数据与演示说明
- v0.0.3 的公开结果为 examples/paired_benchmark.md 及仅含排班种子、组别和数值指标的 examples/paired_501_metrics.csv。
- v0.0.4 公开 examples/repair_results.md、repair_501_metrics.csv 和 select_501_metrics.csv（后两者同属 examples/），只发布合成实验的说明和指标。
- v0.0.5 公开 examples/preference_results.md 和 examples/preference_501_metrics.csv，只含合成需求说明和经过核对的指标。
- v0.0.6 新增 docs/V0.0.6.md 与原创合成输入 examples/consult_input.json。新增程序和测试沿用上述源码范围。
- docs/ARCHITECTURE.md、DECISIONS_AND_OPEN_QUESTIONS.md、ROADMAP.md、OPEN_SOURCE_LANDSCAPE.md、PUBLIC_RELEASE_CHECKLIST.md
- references/README.md（仅索引说明，不包含原文）

## 永不自动进入公开快照

references/private/、reports/、.hermes.md、runtime/、数据库、.env、密钥、模型文件、用户输入、原始分享链接/会话ID、本机绝对路径和任何其他项目文件。公开源码不能依赖私有参考才能运行。

## 发布门

1. 生成精确文件白名单，不打包整个项目目录。
2. 检查许可证范围和第三方素材，不把原文再授权为MIT。
3. 扫描私有路径、账号、凭证、源会话标识和其他项目指纹。
4. 在仅含公开白名单文件的隔离临时目录复跑测试与合成demo。
5. 单独验证wheel/sdist内容；gitignore并不约束所有打包器。
6. 保存源码SHA-256与验证报告，未经验证不标PASS。
7. 用户明确授权后才创建远程仓库/上传；公开路径与私有研究工作区分开。

首次公开发布已获项目发起者授权。

### Windows 上复跑含证书测试的核心验收

若使用 Git for Windows 的标准目录布局，可在 PowerShell 中执行下列命令。环境变量仅对当前会话生效。若使用独立安装的 OpenSSL，可将变量指向该程序。

```powershell
$gitRoot = Split-Path (Split-Path (Get-Command git.exe).Source)
$env:ROUNDTABLE_TEST_OPENSSL = Join-Path $gitRoot 'usr/bin/openssl.exe'
if (!(Test-Path -LiteralPath $env:ROUNDTABLE_TEST_OPENSSL -PathType Leaf)) {
    throw 'Set ROUNDTABLE_TEST_OPENSSL to the installed OpenSSL executable.'
}
python tests/verify_core.py
```

在本地报告 `reports/core_verification.md` 中核对完整测试结果、双向证书测试的执行状态和 SQLite 独立回读。回环网络连接与临时文件替换需要在测试环境中可用。

2026-11-23 对提交 `b6a745f1df6858993ae8bd01c8d4660f28239c85` 做了源码快照复验。用 `git archive --format=zip` 从该提交导出 79 个文件，解压后的初始文件名与提交树逐项一致。本次 ZIP 的 SHA-256 为 `0fe8e6a97ef3d376154988a1e86c35fa452a5606d9da5fa7e95d114d3e5ea7fc`。在解压目录运行上述核心验收，80 项测试全部通过，包含双向证书测试、构建禁用检查和同机双节点恢复。合成演示及 SQLite 独立回读通过，重复运行后原库摘要保持一致。

2026-11-23 复验提交 `ef0438870e2e7a62c010082f1c01b9b0030c6a31`。源码 ZIP 的 SHA-256 为 `6ead9b62dd70a28b9d323d786a632391e946ea7ac0c6e395072c4392ffa5461d`，解压时核对的 81 个文件与提交树一致。清空 PYTHONPATH 并启用临时证书测试后，Windows 同机环境下 87 项回归全部通过，无跳过；覆盖续议副本、保留原投票、双端重发与结算以及在线分页对账。合成演示、SQLite 完整性与外键核验通过，重复执行时原库摘要一致。本次为主分支源码快照验收。
2026-11-23 对主分支提交 `3196347` 完成隔离源码回归。ZIP 的 SHA-256 为 `71696e96ac6f795172dd2a2753d218c2b80faea5fe2b47e68016766baa02fd27`，解压后的 83 个文件与该提交的文件清单一致。启用 OpenSSL 并清空 PYTHONPATH 后，93 项测试全部通过，无跳过。覆盖按规则选择与执行审查，以及回执丢失、服务撤下后的恢复。合成演示进入 RESOLVED，剩余 2 credits，SQLite 完整性和外键检查通过，重复运行后原库摘要一致。
