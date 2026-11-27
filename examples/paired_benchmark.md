# Scheduling conflict detection on nine rosters (v0.0.3)

[中文报告](#v003-排班对照实验)

For these four scheduling rules, the expert alone found every violation on all nine rosters. Giving the model the expert's findings reduced errors on every roster compared with asking the model alone. Model review still introduced errors on three rosters compared with accepting the expert's findings directly.

| Participant producing the answer | Missed violations | False positives | Total calls | Total credits |
| --- | ---: | ---: | ---: | ---: |
| Model alone | 312 | 77 | 9 | 9 |
| Rule expert alone | 0 | 0 | 9 | 9 |
| Model reviewing the expert's findings | 3 | 1 | 18 | 18 |

On the three largest rosters (22 shifts each), expert input eliminated all 234 misses and 40 false positives from the model-only answers for 3 additional credits and about 4.48 additional seconds of participant time in total. Each size had only three rosters, so this comparison cannot establish a general roster-size threshold.

### Reproduce the comparison

Start Ollama with `qwen3.5:9b` installed. From the repository root, choose a fresh output directory and run

```sh
python -m examples.benchmark_consult reports/paired-reproduction --model qwen3.5:9b --first-seed 501 --repetitions 3
```

The three arms receive the same rosters. The benchmark alternates the order of the two model-based arms and records 18 model calls and 18 expert calls on a successful run. Compare your `results.csv` with the [27 reference rows](paired_501_metrics.csv). The per-rule columns use P for worker overlap, V for room overlap, R for insufficient rest and L for excessive continuous work. Each returned call costs one local credit. Seeds fix the inputs. Model outputs and times can vary. The reference run used Ollama 0.40.16, model digest prefix `6488c96fa5fa` and temperature 0.

To see what the additional call bought on each roster, run the offline comparison on the reference CSV or substitute your own results file.

```sh
python -m examples.summarize_detection examples/paired_501_metrics.csv
```

Each comparison subtracts the single participant's result from the roundtable's. Negative values for missed findings or false positives mean fewer errors. The by_rule breakdown shows that, relative to the tool, the roundtable missed one excessive continuous-work block on seed 502 and two on seed 505. On seed 503 it reported one extra worker overlap. The other six matched the tool at one additional credit each. If a run stopped mid-roster, the report lists the missing arms for that roster.

For an interrupted run, pass its saved design to include planned rosters with zero recorded results. All three arms appear as missing for those rosters.

If the model service raises an error during a request, inspect the run directory's `SEED-ARM.json`. The `calls` list contains completed responses. The separate `failed_attempt` records the request, elapsed time and error type. Its token counts are unknown. The `credits_spent` field records the charges already posted for this roster and arm. The failed arm has no scored CSV row.

```sh
python -m examples.summarize_detection reports/paired-reproduction/results.csv --design reports/paired-reproduction/design.json
```

## v0.0.3 排班对照实验

需要模型提交最终清单时，给它一次排班专家调用，在本轮九张表上逐张减少了错误。收益最大的是 22 班次档：三张表共多用 3 credits、4.48 秒，消除了 234 项漏报和 40 项误报。若目标只是判定这四条明确规则，单工具在九张表上已经全部正确。增加模型复核后，六张表持平，三张出现了新错误：种子 502、505 各漏掉一项、两项连续工作超时，503 多报了一对人员冲突。

## 按难度汇总

每行合计三张表。耗时为成员调用耗时之和，单位为秒；输入和输出 token 取自 Ollama 返回值。每个成员成功返回扣 1 个本地 credit。两种单成员方式每张表调用一次，圆桌先调用专家，再请模型复核。

| 班次数 | 方式 | 漏报 | 误报 | 调用 | 输入 / 输出 tokens | credits | 秒 |
|---:|---|---:|---:|---:|---:|---:|---:|
| 7 | 单模型 | 20 | 10 | 3 | 726 / 367 | 3 | 3.991 |
| 7 | 单工具 | 0 | 0 | 3 | 0 / 0 | 3 | 0.00025 |
| 7 | 圆桌 | 1 | 1 | 6 | 885 / 139 | 6 | 1.718 |
| 11 | 单模型 | 58 | 27 | 3 | 930 / 585 | 3 | 5.529 |
| 11 | 单工具 | 0 | 0 | 3 | 0 / 0 | 3 | 0.00024 |
| 11 | 圆桌 | 2 | 0 | 6 | 1268 / 306 | 6 | 3.057 |
| 22 | 单模型 | 234 | 40 | 3 | 1491 / 306 | 3 | 3.635 |
| 22 | 单工具 | 0 | 0 | 3 | 0 / 0 | 3 | 0.00053 |
| 22 | 圆桌 | 0 | 0 | 6 | 2392 / 892 | 6 | 8.114 |

逐表、逐规则数据见 [27 行实测指标](paired_501_metrics.csv)。P、V、R、L 分别表示人员重叠、房间重叠、休息不足、连续工作超时。逐小时评分器计算标准答案，同一对象触发不同规则时分别计数。

## 复现

在本机 Ollama 中准备好 `qwen3.5:9b` 并启动服务，从项目根目录运行：

```bash
python -m examples.benchmark_consult reports/paired-reproduction --model qwen3.5:9b --first-seed 501 --repetitions 3
```

输出目录必须是新路径。每档生成三张不同排班表，三种方式检查完全相同的输入；单模型独立作答，圆桌模型能看到专家提案。程序交替安排单模型和圆桌的先后顺序。共调用模型 18 次、专家 18 次。输出包含 results.csv、设计参数、逐组请求和回复，以及 SQLite 账本。离线运行回归测试可用 `python -m unittest discover -s tests -v`。

参考运行使用 Ollama 0.40.16、模型摘要前缀 `6488c96fa5fa`、温度 0。种子固定输入；模型版本、运行环境和缓存会影响输出或耗时。每档只有三张表，这些结果支持本轮配对比较，还不足以确定通用的班次数阈值。
