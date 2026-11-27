# Choosing a schedule from verified candidates (v0.0.5)

[中文报告](#v005-自然语言排班偏好)

**An extra model call improved on the expert's default for two of nine rosters.** On seed 506 it gained one preference point, and on 507 it gained seven. Both gains came with the same number of changed shifts. When the priorities and exceptions were already encoded as rules, the rules captured both gains and reached the best available score on all nine rosters.

| Selector | Hard constraints passed | Preference points | Shifts changed | Calls / credits | Input / output tokens | Member call time |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Expert default | 9/9 | 31/63 | 90 | 9 / 9 | 0 / 0 | 0.32 s |
| Rule ranking | 9/9 | 42/63 | 94 | 9 / 9 | 0 / 0 | 0.32 s |
| Model selection | 9/9 | 34/63 | 94 | 18 / 18 | 8,878 / 100 | 13.89 s |

Each selector saw the same feasible schedules for each roster. The model read the original Chinese request and returned a candidate ID. An independent scorer awarded 4, 2 and 1 points for the high, medium and low priorities, applying exceptions before scoring. Ties favored fewer changed shifts and then the lower candidate ID. The timing covers member calls and excludes the human work of encoding the rules.

- **501 and 505:** Rule ranking gained one and two points respectively over the default, at the cost of moving two more shifts on each roster. The model missed both improvements.
- **502 and 508:** Even the best candidates scored only zero and one point respectively. Improving these scores requires finding other feasible schedules or establishing which requests can be met together.
- **503, 504 and 509:** The default already reached the best available score. The model matched the default on 503, moved two extra shifts for the same score on 504, and lost five points while moving one extra shift on 509.

These results cover three fixed preference briefs and nine synthetic rosters. The best score among generated candidates is an upper bound for a selector using those candidates. Other feasible schedules may score higher. [Per-roster scores, priority flags and usage](preference_501_metrics.csv) · [Independent scoring rules](preference_rubric.py)

### Reproduce the results

From the repository root with Python 3.11 or later, run the first command to check the published scores and exception boundaries offline. For a new model run, start Ollama with `qwen3.5:9b` installed and give the second command a fresh output directory.

```sh
python -m unittest discover -s tests -p test_preferences.py -v
python -m examples.preference_benchmark reports/preference-repro --model qwen3.5:9b
```

The benchmark uses seeds 501–509 and calls the model once per roster. Compare the new `results.csv` with the published measurements. Model choices, token counts and timing may vary.

### English-language trial

To send English briefs and selection instructions, run the benchmark in a separate directory and reconcile its receipts offline:

```sh
python -m examples.preference_benchmark reports/preferences-en --language en --model qwen3.5:9b
python -m open_roundtable verify-batch reports/preferences-en
```

In one English run with Ollama 0.40.18, all nine model choices passed the hard constraints. The model gained one point over the default on 506 and seven on 507 without moving additional shifts. It lost five points on 501 and six on 504. It tied the default on the remaining five rosters, moving one additional shift on 505. Rule ranking reached the candidate optimum on all nine.

The English model arm earned 28/63 points with 96 changed shifts, 18 calls (nine expert, nine model), 18 credits, 8,917 input tokens, 99 output tokens and 14.53 seconds of member call time. The default earned 31/63 with 90 changed shifts and the rules earned 42/63 with 94. Each used nine calls and nine credits. All 27 rows reconciled and every model call returned a valid choice. The English and historical Chinese runs used the same seeds and scoring rules. A single run per language cannot isolate a language effect.

### Does a wider candidate search help?

With the same English briefs and nine rosters, preference-guided search raised the rule selector's total from 42 to 53 points out of 63 while reducing changed shifts from 94 to 91. Search uses the encoded priorities to try new start times and verify the resulting schedules. The largest gains came on rosters where the original candidates left little room for improvement.

| Seed | Rule points, original → expanded | Changed shifts, original → expanded | Model points with expanded candidates |
| --- | ---: | ---: | ---: |
| 501 | 7 → 7 | 5 → 3 | 6 |
| 502 | 0 → 5 | 4 → 5 | 0 |
| 503 | 5 → 5 | 4 → 4 | 5 |
| 504 | 7 → 7 | 6 → 6 | 3 |
| 505 | 3 → 5 | 10 → 8 | 5 |
| 506 | 7 → 7 | 8 → 8 | 0 |
| 507 | 7 → 7 | 19 → 19 | 2 |
| 508 | 1 → 5 | 19 → 19 | 1 |
| 509 | 5 → 5 | 19 → 19 | 5 |

For 508, expanding the search bought four points without moving another shift. For 502, five points cost one extra changed shift. On both rosters the model passed over the better candidate. The rule arm used nine calls and nine credits in each run, with member call time rising from 0.33 to 1.24 seconds.

With expanded candidates, model selection earned 27 points and changed 96 shifts. It used 18 calls, 18 credits, 14,664 input tokens, 99 output tokens and 5.26 seconds of member call time. The default scored 31 with 90 changed shifts. All 27 selections passed the hard constraints. Each configuration ran once, so the measured times do not establish a speed advantage.

```sh
python -m examples.preference_benchmark reports/preferences-en-expanded --language en --expand --model qwen3.5:9b
python -m open_roundtable verify-batch reports/preferences-en-expanded
```

## v0.0.5 自然语言排班偏好

相对专家默认方案，模型只在 506、507 两张表上提高了得分，两张都无需多改班。人工编码的规则排序在九张表上均达到候选内最优，并能在 501、505 找到模型漏掉的改善机会。502、508 即使选到最好候选，也分别只有 0 分和 1 分，继续改善需要扩充候选或检验硬约束下的可行性。

## 需求和评分

每档 7、11、22 个班次，各使用三张合成表。每档依次使用三类需求：(1) 接孩子与晚班覆盖冲突，原班跨午夜则保留原开工时间；(2) 早班车与晚班人手冲突，原定凌晨开工者保留专车预约；(3) 保留预约与延迟交接冲突，原班至少八小时则允许同日改时。完整原文与独立判分逻辑见 [评分器](preference_rubric.py)。

三组收到相同的可行候选。默认组取专家编号 0；规则组按事先人工编码的高、中、低优先级排序；模型组收到中文需求和候选事实，只能返回编号。选择后，独立评分器分别给满足的高、中、低项计 4、2、1 分，例外替换对应条件。同分优先少改班，再选较小编号；另用逐小时算法复查硬约束。

## 九张表的结果

每格为「偏好分 / 改动班次数」，每表满分 7 分。规则列给出了已生成候选内的上限。

| 种子 | 班次 | 工具默认 | 规则排序 | 模型选择 | 判断 |
|---|---:|---:|---:|---:|---|
| 501 | 7 | 6 / 3 | 7 / 5 | 6 / 3 | 规则可多改两班换 1 分 |
| 502 | 7 | 0 / 4 | 0 / 4 | 0 / 4 | 没有候选满足任何一项 |
| 503 | 7 | 5 / 4 | 5 / 4 | 5 / 4 | 默认已达候选上限 |
| 504 | 11 | 7 / 6 | 7 / 6 | 7 / 8 | 模型同分，多改两班 |
| 505 | 11 | 1 / 8 | 3 / 10 | 1 / 9 | 规则可多改两班换 2 分；最高优先项仍未满足 |
| 506 | 11 | 6 / 8 | 7 / 8 | 7 / 8 | 模型和规则均多得 1 分 |
| 507 | 22 | 0 / 19 | 7 / 19 | 7 / 19 | 模型和规则均多得 7 分 |
| 508 | 22 | 1 / 19 | 1 / 19 | 1 / 19 | 所有候选最高只满足低优先项 |
| 509 | 22 | 5 / 19 | 5 / 19 | 0 / 20 | 模型丢失高优先项且多改一班 |

| 策略 | 硬约束通过 | 偏好分 | 满足最高优先项 | 改动班次 | 调用 / credits | 输入 / 输出 tokens | 调用耗时 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 默认 | 9/9 | 31/63 | 5/9 | 90 | 9 / 9 | 0 / 0 | 0.32 秒 |
| 规则 | 9/9 | 42/63 | 6/9 | 94 | 9 / 9 | 0 / 0 | 0.32 秒 |
| 模型 | 9/9 | 34/63 | 5/9 | 94 | 18 / 18 | 8878 / 100 | 13.89 秒 |

本轮固定需求下，已编码规则覆盖了模型收益，耗时只含成员调用。比较未计入人工编写规则的成本。候选上限也不等同于所有合法排班的上限；例如 503、509 的短班同时要求保留 C 的准确开工时间和延后至少两小时，这两项彼此冲突，增加候选也无法同时满足。[逐表、逐优先项及消耗记录](preference_501_metrics.csv)

## 复现

使用 Python 3.11+，从仓库根目录运行。第一条命令离线验证评分边界、公开结果和规则最优性。第二条需要本机 Ollama 已安装 qwen3.5:9b，输出目录必须为新路径。

```bash
python -m unittest discover -s tests -p test_preferences.py -v
python -m examples.preference_benchmark reports/preference-repro --model qwen3.5:9b
```

种子固定为 501–509，每表调用一次模型。输出包含评分器源码指纹、模型摘要、请求与回复、SQLite 账本和 results.csv。输入与判分可离线重建；模型选择、tokens 和耗时可能随运行环境变化。
