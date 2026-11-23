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
