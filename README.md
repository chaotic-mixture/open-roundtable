# Open Roundtable / 开放圆桌

**An experimental, composable roundtable runtime for heterogeneous participants.**

[English quick start](#english-quick-start) · [中文指南](#中文指南)

For measured results on when a second participant helps, see the [scheduling experiment index](PROJECT_INDEX.md#scheduling-experiment-results).

## English quick start

Open Roundtable lets models and rule-based experts work on the same proposal. A SQLite ledger records each participant’s messages, votes and credit use so you can inspect how a decision was reached.

With Python 3.11 or later, run the offline demo from the repository root. It uses the Python standard library and synthetic inputs.

```sh
python -m open_roundtable demo --db roundtable-demo.sqlite3
```

The demo prints a JSON report. Look for `RESOLVED` in the topic state and a remaining balance of 2 credits. Choose a fresh database filename for each run so earlier results stay available for inspection.

To revisit the demo later, list the topics saved in its database. Find the row whose state is `RESOLVED`, then copy its `id` in place of `TOPIC_ID` to read the messages, their costs and the recorded votes.

```sh
python -m open_roundtable topics roundtable-demo.sqlite3
python -m open_roundtable show roundtable-demo.sqlite3 TOPIC_ID
```

To try node discovery, run `python -m examples.registration_demo`. It starts temporary services on your machine. The consumer finds no nodes at first, sees B after publication, loses B after withdrawal and sees B again after republication. The demo checks the consumer's ledger to confirm that rediscovery leaves the earlier consultation permission and quota revoked. [Registration example](examples/registration_demo.py)

When a directory withdraws and republishes B at the same address between refreshes, its next snapshot carries a new publication token. A clears B's old permission and quota on refresh. An explicitly renewed grant survives later refreshes of the same publication. The demo reports `remaining_after_missed_withdrawal: null`. Older directories that only serve unversioned snapshots can miss this transition. To revoke the grant directly, run `python -m examples.peer_node runtime/consumer permission B revoke` on A.

Run `python -m examples.peer_node runtime/consumer permission-status B` on A to inspect its local decision. A discovered node can have no grant. With a grant still in place, `remaining: 0` and `can_submit: false` mean the peer has exhausted its quota for new requests. Retrying an identical, already completed request can still retrieve its saved receipt. For an active grant, a null remaining count means unlimited uses.

Explicitly revoking the grant also closes the receipt-retry path for that peer. The receiving node keeps its existing ledger entry for reconciliation. If the operator grants access again and the original topic is still shared, replaying an identical completed request returns its saved receipt and preserves the new quota for fresh work.

### Repair a schedule with your own preferences

To check a roster locally before sharing it, save the following request as `audit.json` and run `python -m open_roundtable audit audit.json`. The offline command reads the file and prints findings using the same validator and scheduling expert as a receiving node.

```json
{"operation":"schedule-audit-v1","rules":["R","V"],"shifts":["Axv:22-26","Bxu:29-33"]}
```

Each shift starts with its ID, worker and room, followed by start and end hours from the beginning of the week. Here the expert reports `x@26-29` under minimum rest (`R`): worker x has only three hours between shifts. The room-overlap findings (`V`) are empty.

For a scripted check, run `python -m open_roundtable audit audit.json --fail-on-findings`. Exit status 0 means all requested checks passed, 1 means the JSON report contains findings, and 2 indicates a request or input error. Without the flag, a completed audit returns 0 even when it reports conflicts.

The [English scheduling request](examples/consult_input.en.json) assigns Alex two overlapping shifts in the same room. Copy the file and edit the shift IDs, workers, rooms and times. Times are integer hours from the start of the scheduling week. Write the tradeoff in `preference`. The optional `criteria` array lists machine-checkable conditions in priority order. This example counts from midnight at the start of Saturday, so hour 18 meets the pickup deadline. A `delta` of zero means keeping the original start time.

The model receives your request, including the original preference text, alongside the verified candidate schedules. The expert scores the conditions you explicitly put in `criteria`. For each condition, name a shift ID, choose a field below and give inclusive integer bounds `min` and `max`.

| Field | What the expert checks | Example |
| --- | --- | --- |
| `start` or `end` | Absolute hour from the start of the week | An `end` from 0 to 18 meets the example's Saturday deadline. |
| `hour` | Start hour within a day (0–23) | A range of 0 to 11 accepts starts before noon on any day. |
| `day` | Day of the shift's start, counting from 0 | In the Saturday-based example, a shift starting Sunday has day 1. |
| `delta` | Candidate start minus original start, in hours | A range of -2 to 0 allows a shift to start up to two hours earlier. |

List your most important condition first. When ranking verified candidates, the expert compares the conditions in order. At the first difference it prefers the plan meeting that condition. If all flags match, it prefers fewer shifted start times.

With a local Ollama service running `qwen3.5:9b`, use a fresh database path for the consultation:

```sh
python -m open_roundtable consult examples/consult_input.en.json --db weekend.sqlite3 --model qwen3.5:9b
```

The rule expert generates feasible candidates and the model chooses among them. Inspect `criteria_met` to see which preferences the proposal satisfies. A disagreement can leave the consultation paused for revision. [Chinese scheduling request](examples/consult_input.json)

If the consultation is `PAUSED` and the expert voted against the first proposal, revise it in a fresh ledger:

```sh
python -m examples.revise_panel_proposal weekend.sqlite3 weekend-revised.sqlite3
```

The revision reuses the verified candidates and ranks them by the declared priorities. In one run of the English example, `previous.criteria_met` was `[false, true]`: the model kept the late shift’s start but missed the early shift’s deadline. Version 2 scored `[true, false]`, meeting the higher priority. Each proposal moved one shift relative to the original roster, but switching between proposals changed both shifts’ start times. The expert approved version 2; the ledger stayed `PAUSED` pending a new vote. This step runs offline and preserves the original ledger.

### Compare direct and delegated scheduling decisions

Use the original `weekend.sqlite3` from the consultation above. Run the same request with the same model in a fresh ledger, this time delegating the decision to a child roundtable. Then compare the two saved decisions offline:

```sh
python -m open_roundtable consult examples/consult_input.en.json --db weekend-panel.sqlite3 --model qwen3.5:9b --nested
python -m examples.compare_panel_receipts weekend.sqlite3 weekend-panel.sqlite3
```

Each ledger must contain one returned model call. Compare `criteria_met` in priority order, then `changed_shifts` to see how much of the roster moved. The report also gives input and output tokens and model-call time for each run. In the delegated run, `credits` measures work already performed by the expert and model. A null `settled_credits` means the parent has yet to settle that work.

### Recover a consultation after a disconnect

Start a local Ollama service with `qwen3.5:9b` installed. From the repository root, run the following command with a new output directory. The example uses OpenSSL to issue temporary certificates. If `openssl` is outside your PATH, supply its executable path after `--temporary-tls`.

```sh
python -m examples.two_node_demo run reports/recovery-first --language en --temporary-tls openssl --bind-offer --fault after-commit
```

Nodes A and B discover each other through a directory on the same machine. A sends a synthetic scheduling request. B runs the rule expert and model, records the charge, then stops before A receives its receipt. The demo withdraws B’s service offer, restarts B, and retries the original request.

To check the result later, run the verifier against the same directory. It reads the saved evidence and works with Ollama stopped.

```sh
python -m examples.two_node_demo verify reports/recovery-first
```

A successful recovery reports two deliveries, one expert call, one model call and 2 credits in total. Both nodes record the same charge. For this fault window, `receiver_credits_at_disconnect` is already 2. Check `private_stores_checked` for A, B and the directory, and `private_markers_found` for the number of synthetic private markers found in the inspected records. [Walkthrough in Chinese](#phase-2-双节点断线恢复实验) · [Demo and verifier](examples/two_node_demo.py)

### Reconcile two nodes' receipts offline

For a run with ordered preferences, compare `criteria_met` and `best_candidate_criteria_met` from left to right. The first differing flag identifies the highest priority on which the selected and best available schedules differ. When the flags match, compare `changed_shifts` and `best_candidate_changed_shifts` to see whether the same preferences could be met with fewer changes.

Add `--with-criteria` to a fresh recovery run to send the fixture's ordered preferences with the roster. The verifier recomputes the selected schedule's preference score and checks the expert vote against the candidate list. In an English run, the selected schedule preserved the late shift's start but missed the higher-priority pickup deadline. A received B's `PAUSED` decision after reconnecting. Both ledgers recorded two credits for one expert call and one model call.

When the expert dissents, pass the completed recovery directory to the revision command. It verifies the two nodes' receipts and opens B's matching consultation ledger. Choose a fresh destination for the revised ledger.

```sh
python -m examples.revise_panel_proposal reports/recovery-first reports/recovery-first/revision-v2.sqlite3
python -m examples.two_node_demo verify reports/recovery-first
```

In the English run above, version 2 met the pickup deadline and moved one shift relative to the original roster. The expert approved it in the local copy. A second vote is still needed there. The network verifier continues to report the original decision that A received, with its original charge. The revision command performs no model call or network delivery.

To exercise a disconnect after the consultation result is saved but before B posts the network charge, use a fresh directory:

```sh
python -m examples.two_node_demo run reports/recovery-before-receipt --language en --temporary-tls openssl --bind-offer --fault before-receipt
python -m examples.two_node_demo verify reports/recovery-before-receipt
```

In two independent same-machine runs with the English request and local `qwen3.5:9b`, B had posted zero credits at the `before-receipt` disconnect and two at the `after-commit` disconnect. Both recovered to two posted credits after two deliveries, one expert call and one model call. In both runs B had used its one-request allowance before disconnecting and its remaining allowance stayed at zero after replay.

After a consultation, B can export its statement for A. Use B's node directory and a fresh output filename:

```sh
python -m examples.peer_node runtime/B statement A B-for-A.json
```

Deliver the file to A through a channel both operators trust. On A, compare it with the local ledger:

```sh
python -m examples.peer_node runtime/A reconcile B B-for-A.json
```

For a single settled job with matching receipts, expect `{"matched": 1, "differences": []}`. The statement carries job IDs, provider names, test credits and receipt hashes. A missing entry, a pending job or a different receipt produces a difference and a nonzero exit status. If both sides report the job as pending, zero posted credits alone cannot tell you whether inference ran.

If the receiver stopped during inference, run `python -m examples.peer_node runtime/A account` on A and the corresponding command with `runtime/B` on B. Look for the same `job` ID under `pending_jobs` on both sides and check its `consumer` and `provider`. In the interruption test, the model service received one request before B stopped. After restart and retry, both accounts still listed the job as pending with zero posted credits. Offline reconciliation returned `matched: 0` and the reason `pending at both nodes`. Keep that job open for investigation. Reproduce this fault window locally with:

```sh
python -m unittest tests.test_peers.PeerRecoveryTests.test_restart_after_model_dispatch_without_receipt_keeps_job_pending
```

On the provider, run `python -m examples.peer_node runtime/B dispatch-status JOB_ID` with the job ID from the account. The command opens the consultation database read-only and reports the status of each attempt and whether a response was saved. In the interruption test it reports one `dispatching` attempt with `has_response: false` after the retry. The journal records dispatch intent before network I/O. Only the test service's independent request count confirms that this particular call reached it.

Add `--checkpoint` to inspect the attempt and the consultation receipt in the same database snapshot. A saved model response and a saved consultation receipt are separate milestones. `has_consultation_receipt` reports whether the provider saved a receipt for this job; use the two-node verifier to check its contents and reconcile the charge.

For a repair that ended before model selection, inspect `no_plan.feasibility`. An `infeasible` result means the expert ruled out a schedule under the submitted constraints. An `unknown` result leaves feasibility open; a new request with a larger search budget may find a plan. The `nodes` field counts search nodes visited. Replaying the same request returns its saved result.

To increase that budget, change the positive integer `search_nodes` in the scheduling request (default 10,000), then review and share the revised public record before sending it. This value limits the expert's exact search. For example, in a direct search for two four-hour shifts originally starting at hour 8 in the same room with different workers, a budget of 1 returned `unknown`. A budget of 100 found starts at hours 8 and 4 after visiting 9 nodes. This example measures the search routine; the ordinary repair workflow tries its candidate generator first.

A provider can set `max_search_nodes` to a positive integer in its own `node.json`. A new repair request whose search budget exceeds that ceiling is rejected before it uses a consultation allowance. Requests that omit `search_nodes` use the 10,000-node default for this check. The ceiling applies to exact search; model tokens and candidate generation have separate costs.

Before sending a roster, run `python -m examples.peer_node runtime/A admission B --search-nodes 10000` over mutual TLS. A sends its identity and proposed budget. B checks both its current grant and the budget ceiling without reserving quota. A requires confirmation of the exact budget, so an older peer that only checks grants cannot accidentally confirm eligibility for an expensive repair. B checks again when accepting the actual request.

To find providers for that budget, run `python -m examples.peer_node runtime/A find-capability repair reschedule --search-nodes 10000`. Each matching provider receives one admission query across its offers. The same option on `match-capability` filters providers before model selection. When every provider declines, the result lists the declined nodes and records zero selection calls.

### Reconcile receipts online

With mutual TLS configured on both nodes, A can compare its ledger with B's over the network. On B, add `"statement_partners": ["A"]` to the existing `node.json` and restart B's service. This authorizes A to read their shared statement. Consultation grants and quotas are managed separately.

After A has discovered B at its current address, run:

```sh
python -m examples.peer_node runtime/A reconcile-online B
```

A checks B's certificate identity. B checks the caller's certificate against the requested identity and its statement-sharing configuration. The command reports the same match count and differences as offline reconciliation. If a statement changes while pages are being read, rerun the comparison to read a consistent revision.

### Prepare a node for a cross-host trial

The recovery demo runs on loopback. For a trial across machines, set `transport` to `mtls` in each node's `node.json` and set `listen_address` to a specific IPv4 address assigned to that machine. The default is `127.0.0.1`. List every remote HTTPS origin the node will contact in `allowed_origins`, including the directory when it is remote. Each entry includes the explicit port, for example `https://peer-b.example:8443`, with no path or trailing slash.

The `credentials` directory holds `ca.pem`, `node.pem` and `node.key`. The node certificate must cover the hostname or IP used by clients and identify the configured node name with a URI such as `urn:roundtable:peer:B`. The demo issues certificates for localhost and the loopback address. A cross-host acceptance run still needs to demonstrate connectivity, identity checks and lost-receipt recovery on separate machines.

For a directory whose configured node name is `directory`, start its service on the directory host. The following examples assume initialized stores and configured certificates on both machines:

```sh
python -m examples.peer_node runtime/directory serve 8443
```

On the consumer host, replace the example hostname with the directory's certificate-covered hostname. Add that exact origin to the consumer's `allowed_origins`, then fetch the published registrations:

```sh
python -m examples.peer_node runtime/consumer discover https://directory.example:8443 --directory-name directory
```

The directory name is the identity in the certificate URI. A successful empty list means the directory returned no eligible peers. To exercise publication and withdrawal on one machine first, run `python -m examples.registration_demo --openssl openssl`.

To publish provider B, start its configured service with `python -m examples.peer_node runtime/provider serve 8443` on the provider host. In another terminal on that host, create and review the registration. Replace the example hostname with B's reachable hostname throughout:

```sh
python -m examples.peer_node runtime/provider register https://peer-b.example/node https://peer-b.example:8443
python -m open_roundtable.data runtime/provider review-public https://peer-b.example/node
python -m open_roundtable.data runtime/provider share-public https://peer-b.example/node --if-sha256 PROVIDER_HASH
```

Use the hash of the reviewed registration for `PROVIDER_HASH`. On the directory host, allow B's HTTPS origin in the directory configuration, then import the published registration and review the directory's copy:

```sh
python -m examples.peer_node runtime/directory import-registration B https://peer-b.example/node https://peer-b.example:8443
python -m open_roundtable.data runtime/directory review-public https://peer-b.example/node
python -m open_roundtable.data runtime/directory share-public https://peer-b.example/node --if-sha256 DIRECTORY_HASH
```

Use the hash from the directory review for `DIRECTORY_HASH`. Run the consumer's discovery command again to see B. To withdraw this listing, run `python -m open_roundtable.data runtime/directory share-public https://peer-b.example/node --off` on the directory host. The consumer's next discovery refresh removes B from this directory snapshot.

### Keep a private preference and publish a service notice

From the repository root, create two UTF-8 JSON files. In `preference.json`, save `[{"source":"profile","body":"I prefer morning meetings."}]`. In `notice.json`, save `[{"source":"https://example.org/hours","body":"Office hours start at 9."}]`. The HTTPS address identifies the notice. Importing the file reads its contents from disk.

```sh
python -m open_roundtable.data runtime/store-demo init
python -m open_roundtable.data runtime/store-demo import private preference.json
python -m open_roundtable.data runtime/store-demo maintain-private profile
python -m open_roundtable.data runtime/store-demo summary-origin profile
python -m open_roundtable.data runtime/store-demo import public notice.json
python -m open_roundtable.data runtime/store-demo public-feed
```

Use a fresh directory for `init`. The summary should report `ready: true`, mode `sealed`, and model `null`. Sealed maintenance normalizes whitespace locally. The public feed should still be `[]` at this point.

```sh
python -m open_roundtable.data runtime/store-demo review-public https://example.org/hours
python -m open_roundtable.data runtime/store-demo share-public https://example.org/hours --if-sha256 HASH
python -m open_roundtable.data runtime/store-demo public-feed
```

Replace `HASH` with the SHA-256 from the review output after checking the notice text. The feed should now contain only the office-hours notice. To withdraw it, run `python -m open_roundtable.data runtime/store-demo share-public https://example.org/hours --off`. [Store and permission guide in Chinese](#公开资料和个人资料)

### Choose where a private record can be processed

The private store starts in `sealed` mode. Its policy controls how a record's summary is produced. In `sealed` mode the process normalizes whitespace on the same machine. In `local` mode it sends the record body to the configured local Ollama service. In `api` mode it sends the body to the configured HTTPS endpoint when maintenance runs.

For example, to allow local-model maintenance by default while keeping the example's `profile` record sealed, set its individual limit first:

Before changing the policy, `python -m open_roundtable.data runtime/store-demo private-status --by-mode` reports how many pending records currently use each mode. Run it again after a change to compare the counts. The command reads the whole backlog without sending record contents or advancing maintenance.

To preview only the next batch, use `python -m open_roundtable.data runtime/store-demo maintain-pending --limit 1000 --preview`. On one local store with 10,000 pending synthetic records (three sealed, three local, the rest inheriting API mode), grouping the policy lookups reduced median preview time from 5.28 to 2.21 ms for a 1,000-record batch and from 36.28 to 6.20 ms for the full backlog. A ten-record batch took about 1.79 ms with either implementation. Each measurement used 20 warmed runs per implementation in alternating order against the same database. These times measure preview only.

A separate local test measured cursor indexing on two identically populated stores with 100,000 synthetic records each. Every eighth record was pending and all used sealed mode. With the cursor halfway through the store, previewing ten records took 5.50 ms before indexing and 1.16 ms afterward. Reading all 12,500 pending records took 10.75 and 3.46 ms respectively. Near the end of the store, a ten-record preview that wrapped to the beginning took 5.40 and 1.16 ms. Counts matched on every run. These are medians of 20 warmed previews per version, with execution order alternated and initial index creation excluded.

```sh
python -m open_roundtable.data runtime/store-demo record-limit profile sealed
python -m open_roundtable.data runtime/store-demo policy local --model qwen3.5:9b
python -m open_roundtable.data runtime/store-demo preview-private profile
```

The preview should show `store` as `local` and the effective `mode` as `sealed`, with a null destination. Preview reads the policy without sending the record body. Use `record-limit profile inherit` to let the record follow the store policy again. A sealed store keeps every record sealed, including records with an individual local-model limit.

To preview API maintenance, keep the individual limit on `profile`. Replace the example model and HTTPS endpoint with your provider's values before running maintenance. The following commands only configure the store and inspect its routing decision:

```sh
python -m open_roundtable.data runtime/store-demo policy api --model example-model --endpoint https://api.example.org/v1/chat/completions
python -m open_roundtable.data runtime/store-demo preview-private profile
python -m open_roundtable.data runtime/store-demo policy sealed
```

For `profile`, the preview reports `store: api`, `mode: sealed` and a null destination. If you preview another private record that inherits the store policy before the final command, its destination shows the API origin. The final command restores sealed maintenance for the whole store. When you choose to perform API maintenance, provide the service credential through `ROUNDTABLE_API_KEY` if required.

To inspect the quality of local-model maintenance, run `python -m examples.private_summary_cases` with Ollama serving `qwen3.5:9b`. The example creates a temporary private store and prints each synthetic source alongside its saved summary. Check whose preference is known, whether both conditions on a late shift survive, and whether a pending pilot remains tentative. Use `--language zh` for the original Chinese cases.

In one English run with `qwen3.5:9b`, the late-shift summary preserved both conditions (Friday and a colleague handling pickup). The pilot summary kept the current roster and the conditional change, but omitted the explicit statement that approval was pending. The preference summary attributed early shifts to Jo and kept Lee's preference unknown, but introduced the pronoun “his” for Lee. After the prompt was revised to preserve explicit decision status, avoid inferred gender and copy already concise records, a second run with the same model reproduced all three originals verbatim. This comparison covers one run per prompt on short synthetic records. The three original Chinese short records were also copied verbatim in a follow-up run.

For a longer meeting record, run `python -m examples.private_summary_cases --handoff`. Add `--language zh` for the Chinese version. Check whether the summary preserves who reported a preference, whether the reporter stated one, what schedule applies today, and whether the possible change still requires approval.

In one local run per language, the English record shrank from 496 to 194 characters and the Chinese record from 131 to 55. Both retained the current roster, pending approval and the conditional move to Wednesdays next month. The English summary changed Lee's undisclosed preference into a claim that Lee had no preference and omitted Lee's role as messenger. The Chinese summary preserved both details. A single bilingual example cannot establish a general difference between languages.

Two follow-up English runs used the same meeting record and model. An instruction to keep unstated preferences unknown still produced a 200-character summary claiming Lee had stated a lack of preference. Asking the model to copy the clause describing what the messenger disclosed produced a 273-character summary that preserved Lee's role and the wording “gave no personal preference.” It also retained Jo's early-shift preference, the current roster, pending approval and the uncertainty of the future change. The latter instruction is now used for model-based maintenance. With the adopted instruction, a second English run also preserved those facts, but used 439 characters instead of 273 for the same 496-character input. The two summaries shortened the record by 11% and 45%, respectively. One Chinese run with that instruction used 90 of 131 characters and retained the messenger, the undisclosed preference, the current roster and the pending decision. These counts describe repeated processing of one meeting scenario.

Each output row includes the requested `model`, the service's `reported_model` when available, and the maintenance `mode` read back from SQLite. In a local English run on 2026-11-27, all three rows recorded `qwen3.5:9b` for both model fields. The summaries preserved Jo's ownership of the preference and both conditions on Chen's late shifts. The pilot summary retained pending approval and the tentative change, but its “otherwise” phrasing made the current roster's present status less explicit. These observations cover one generation per record in that run.

After the prompt was changed to ask for a separate sentence about the current arrangement, the same model produced “The current roster still applies while approval is pending. If the pilot is approved, Zhao might switch to Wednesday shifts next month.” The ownership and late-shift qualifications also survived this rerun. Each case was sampled once with the revised prompt.

The same revised prompt was also run once on each Chinese case with `qwen3.5:9b`. The stored summaries repeated the ownership and pending-pilot records verbatim. The late-shift summary shortened the wording while retaining both Friday and a colleague picking up the child as necessary conditions. All three receipts reported the requested model.

If batch maintenance fails, run `python -m open_roundtable.data runtime/store-demo maintain-pending --limit 2 --diagnostics` to retry up to two pending records. The report counts I/O or HTTP failures under `io_or_http`, individual record constraint failures under `storage_constraint`, and validation failures under `validation`. It omits source names, record contents and exception messages. This command performs maintenance under each record's effective policy.

### Compare ways to find a scheduling expert

Run `python -m examples.rule_discovery --dispatch` from the repository root. A requester sends nine synthetic rosters to rule-based experts over loopback HTTP. All four providers advertise the same audit tag, but their offers differ in which checks they declare. Each roster produces two JSON lines. Compare `tag` (select by audit tag) with `rules` (also require every requested check). The rule codes are P for worker overlap, V for room overlap, R for minimum rest and L for maximum continuous work.

| Across nine rosters | Tag only | Tag and required rules |
| --- | ---: | ---: |
| Extra providers selected | 21 | 0 |
| Qualified providers missed | 0 | 0 |
| Directory requests | 36 | 36 |
| Requester network requests after discovery | 51 | 30 |
| Receipts matching the independent roster check | 15 | 15 |
| Credits charged | 0 | 0 |

Here `extra` and `missed` count providers relative to the declared requirements. An extra selection triggers an offer check before the requester sends any shifts. Filtering earlier saved 21 such requests in this run. Both modes completed the same audits without calling a model. [Experiment and provider definitions](examples/rule_discovery.py)

## 中文指南

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

#### 排班请求的无解回执

当结果为 `outcome.state = NO_PLAN`，查看 `outcome.feasibility` 决定如何处理。

| 回执值 | 含义 | 后续操作 |
| --- | --- | --- |
| `infeasible` | 已证明当前班表无法满足硬约束。员工或房间的周容量超限时可以直接证明，搜索节点数为零。 | 调整班次、人员或房间，重新共享修改后的议题并发送。 |
| `unknown` | 搜索预算用尽，当前仍无法判断是否有可行方案。 | 如要继续搜索，提高议题中的 `search_nodes`，审核并共享修改后的议题，再发送新请求。 |

两种回执都包含搜索节点数 `outcome.nodes`。本次接单使用一次授权额度，模型调用和回执中的 `credits` 均为零。在原议题仍共享且授权有效时，重发同一请求会取回已保存的回执。修改议题会产生新的请求标识，接收方将重新检查接单额度。

### 公开资料和个人资料

每位客户使用独立目录。以下命令初始化两个 SQLite 文件，公开库保存带 HTTPS 来源的资料，私有库保存该客户的个人记录。运行账本另行保存。

```bash
python -m open_roundtable.data runtime/customer-a init
python -m open_roundtable.data runtime/customer-a import private personal.json
python -m open_roundtable.data runtime/customer-a maintain-private profile
python -m open_roundtable.data runtime/customer-a summary-origin profile
python -m open_roundtable.data runtime/customer-a private-status
python -m open_roundtable.data runtime/customer-a maintain-pending --limit 10 --preview
python -m open_roundtable.data runtime/customer-a maintain-pending --limit 10
python -m open_roundtable.data runtime/customer-a public-feed
```

本机 `qwen3.5:9b` 混合批次实测使用两条合成记录。一条记录描述虚构协调员偏好周二上午开会，另一条仅允许封闭整理。预览得到 sealed 1、local 1、api 0；执行后完成 2 条，再次执行选中 0 条。SQLite 中的模型摘要保留了周二上午和协调员角色，省略了“虚构”这一限定；封闭记录的结果只合并了多余空白。此例验证本机维护流程，摘要保真度仍需按用途核对。

在本机 Ollama 提供 `qwen3.5:9b` 时，运行 `python -m examples.private_summary_cases --language zh` 可逐条比较三条中文合成记录与私库实际保存的摘要。调整摘要提示后，每条各测一次：偏好归属样本保留了小周喜欢早班和转述者小林偏好未知；晚班例外保留了周五与同事接孩子两个必要条件；试点样本保留了尚未获批、排班照旧以及获批后也只是可能变更。三条结果按原文人工核对，样本量不足以估计整体准确率。

批量维护前可用 `--preview` 查看各权限模式下的记录数。正式执行会逐条核对当前权限；个别模型请求失败时继续处理同批其他记录，并以退出码 2 报告部分失败。下次运行从上次选取的位置继续。用 `summary-origin SOURCE` 可查询当前摘要实际使用的模式和模型名；旧摘要的来源信息可能为空。

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

### 接收节点断线后如何查账

| 接收节点退出时点 | 当时接收方网络账本 | 重发同一议题后的实测结果 |
|---|---|---|
| 模型已收到请求，会诊库尚无回执 | 待结算，0 credits | 双方均待核对，模型替身总共收到一次请求，实际推理消耗未知 |
| 会诊库已保存回执，网络账本尚未结算 | 待结算，0 credits | 从会诊库恢复原回执，双方各记录同一笔 2 credits |
| 接收方网络账本已结算，发送方尚未收到回执 | 已结算，2 credits | 返回已存回执，双方各记录同一笔 2 credits |

首行用可计数的模型替身和两个本机节点进程复现，运行 `python -m unittest discover -s tests -p test_peers.py -k restart_after_model_dispatch`。在双方的 `account` 输出中按 `pending_jobs` 的议题编号查找，再用 `statement` 和 `reconcile` 核对。

后两行使用同机双向 TLS、公开能力声明和本机 Ollama 模型 `qwen3.5:9b` 验证，每组实际投递两次，专家和模型各执行一次。以下各次 run 需指定新目录，openssl 需在 PATH 中或替换为程序路径。verify 可在节点和模型退出后运行，会核对两端账本、新演练在重启前保存的回执摘要，以及私有标记是否出现在受检记录中。

```text
python -m examples.two_node_demo run reports/peer-before-receipt --temporary-tls openssl --bind-offer --fault before-receipt
python -m examples.two_node_demo verify reports/peer-before-receipt
python -m examples.two_node_demo run reports/peer-after-commit --temporary-tls openssl --bind-offer --fault after-commit
python -m examples.two_node_demo verify reports/peer-after-commit
```

### Phase 3 本地子圆桌实验

运行 `python -m open_roundtable consult examples/consult_input.json --db panel.sqlite3 --nested` 可用新数据库把排班修复委派给规则专家与本机模型组成的子圆桌。父议题划拨 2 credits。子议题通过表决后将提案和费用交付父议题；存在分歧时保留双方表决，输出中的 settled_credits 为 null。对同一输入分别运行普通会诊和带 --nested 的会诊后，可用 `python -m examples.compare_panel_receipts DIRECT_DB NESTED_DB` 离线比对两份账本。脚本核对模型请求，输出方案是否一致、偏好满足情况、tokens、执行支出和父议题结算金额。

运行 `python -m open_roundtable create DB TITLE CREDITS` 可在新库或已有库中创建父议题，返回的 `id` 用作 PARENT。用 `python -m open_roundtable delegate DB PARENT MEMBER TITLE BUDGET` 委派预算，返回的 `child` 用作 CHILD。用 `python -m open_roundtable join DB CHILD REVIEWER human` 加入评审成员，再对父、子议题分别运行 `python -m open_roundtable start DB ID`。成员可用 `python -m open_roundtable post DB CHILD REVIEWER BODY --cost 2` 登记工作记录及所用的本地测试 credits，省略费用时计 1。重启或提交失败后，运行 `python -m open_roundtable show DB TOPIC` 查看已保存的状态、余额、发言和表决。父议题返回的 delegations 逐项列出子议题、成员、划拨预算 budget 和结算支出 spent；spent 为 null 表示待结算，0 表示已结算且零支出。独立出资的议题因余额不足暂停后，可用 `python -m open_roundtable top-up DB TOPIC AMOUNT` 追加预算，再重试未入账的工作记录。子议题遵循委派时确定的固定预算。子议题通过 propose 和 vote 完成表决后，运行 `python -m open_roundtable settle DB CHILD` 交付提案并取回未用预算。重复同一委派或结算返回已有结果。

在源码根目录运行 `python -m examples.nested_consult nested.sqlite3 AXR:20-26 BXR:24-29`，数据库文件须为新路径。父议题从 4 个测试 credits 中划给子圆桌 3 个。子圆桌执行排班检查并表决，花费 1 个，将已通过的提案返回父议题，退回余下 2 个。父议题再花 1 个独立核验结果，最终余额为 2。父级账本中子圆桌发言的费用包含下级消耗，统计总费用时只汇总同一层。此示例检验本地委派、结算与表决；递归对复杂任务完成质量的收益仍待对照实验。

重复测量可运行 `python -m examples.nested_benchmark reports/nested-repeat-four --repeats 4`。每张表两种方式各先执行两次，逐轮使用独立数据库。复核已有结果时，在相同目录上运行 `python -m examples.nested_benchmark reports/nested-repeat-four --summary-only`，即可从 CSV 汇总两组的结案数、漏报、误报、费用和配对耗时差。一次本机实测中，36 对运行的漏报和误报均为零，每次均消耗 2 credits；子圆桌在 33 对中耗时更长，配对耗时差的中位数为 33.51 毫秒。

运行 `python -m examples.nested_benchmark reports/nested-paired` 可复现直接执行和子圆桌执行的对照。两组使用相同的区间检查器、逐小时复核器和 4 credits 预算。固定九张表各含 7、11 或 22 个班次，每种规模三张；每张表每组运行一次。两组各完成 9/9，漏报与误报均为零，各用 18 credits。本次本机端到端耗时中位数分别为 126.84 ms 和 152.13 ms，包含建库和持久化。对这组可直接脚本化的任务，加一层委派没有提高准确率。结果目录保存逐表 CSV 和可独立回读的 SQLite 账本。

同库历史提案可用 `python -m open_roundtable cite DATABASE TARGET_TOPIC SOURCE_TOPIC` 引入当前议题。输出中的 `citations` 保存来源议题、已通过的版本、标题和正文，便于复查。引用不会增加预算支出或表决席位；需要重新执行工作时可使用子圆桌委派。

### Phase 4 本地贡献与消费账本

对同一次双节点演练，分别运行 `python -m examples.peer_node reports/release-peer-run/A account` 和 `python -m examples.peer_node reports/release-peer-run/B account`。两端输出可按任务编号核对的服务明细，列出供给方、消费方、会诊状态和内部测试 credits。接收节点记录贡献，发起节点记录消费。只有已保存回执的任务计入费用，待处理任务单独计数。此命令离线统计；回执中的会诊状态只表示流程结果，服务质量仍须通过实践反馈评估。

消费方可将实际使用后的观察写入本地 UTF-8 文本，再运行 `python -m examples.peer_node NODE_DIR feedback JOB_ID useful observation.txt`（未起作用时选 `not-useful`）。`python -m examples.peer_node NODE_DIR quality PROVIDER` 汇总本节点收到的服务笔数、已评价笔数和已评价样本中的有用比例。无评价时比例为 null。每张回执最多计一次，观察正文保存在消费方私库。统计反映该消费方的使用经验，尚无跨身份防刷或全网评分。

接收方可用 `permission PEER allow --remaining 3` 把该节点剩余可受理的新会诊设为 3 次。受理新任务与占用一次额度在同一个事务中完成；中断后仍保留已占用额度。额度用尽后仍可重发已受理任务的回执。再次授权会重设余量，省略 `--remaining` 表示不限次数，`revoke` 则撤销整个授权。双节点演练给 A 一次额度，覆盖首次执行与断线恢复。

### Phase 5 本地元议题实验

运行 `python -m examples.meta_consult meta.sqlite3`，使用新的数据库路径。元议题先登记比较方案、九个种子和通过条件，再对同一批候选比较默认选择与优先级排序。种子 601–609 的两组方案均通过全部硬约束。偏好总分从 32/63 增至 44/63，五张表改善、四张持平；602 和 608 分别多改动一个和两个班次。提案、逐表结果与表决保存在本地，运行时默认选择策略保持原样。本实验采用两个确定性脚本角色，结论限于已编码的三类偏好。

本机操作者可为上述元议题单独启用或撤回已验证的排序规则。下列命令里的 TOPIC 是实验输出的 topic 值。选择结果包含方案编号与实际排班，返回前再次检查硬约束。授权依据是本地实验库的写权限；数据库记录每次生效的选择与对应提案版本。每个议题初始使用默认排序。

~~~
python -m examples.selector_policy meta.sqlite3 TOPIC enable
python -m examples.selector_policy meta.sqlite3 TOPIC select 1 Axu:20-25 Byv:21-25 Cxu:24-31
python -m examples.selector_policy meta.sqlite3 TOPIC withdraw
python -m examples.selector_policy meta.sqlite3 TOPIC status
~~~

在同一可信目录上再次运行 `discover` 会用完整快照更新本地节点名单。目录撤回登记或节点更换地址时，旧授权和剩余额度同时失效；以后重新登记需重新授权。有效空目录会清空名单。若获取目录失败或某个登记无法在节点端核实，保留前一次成功同步的名单。

同机节点可在 node.json 中设置 `"transport":"mtls"`，并在各自节点目录提供 ca.pem、node.pem、node.key。客户端与服务器都校验证书链，服务器证书需覆盖回环 IP，受理会诊还要求客户端证书包含与发送节点名一致的唯一 `urn:roundtable:peer:NAME` URI 标识。目录和节点地址须使用 HTTPS。该模式默认监听回环地址，初始目录依赖配置的地址与受信 CA。联系已命名的节点时，还会在发送 HTTP 请求前核对服务器证书中的节点名。测试使用临时证书；设置 ROUNDTABLE_TEST_OPENSSL 为 OpenSSL 程序路径后，运行 `python -m unittest discover -s tests -p test_peer_tls.py` 可复现。

双节点演练可加 `--credentials CERT_DIR` 启用全流程双向 TLS。该目录须含 A、B 和 directory 三个子目录，各含 ca.pem、node.pem、node.key；directory 使用独立的目录服务证书。示例命令为 `python -m examples.two_node_demo run reports/tls-demo --credentials CERT_DIR --fault before-receipt`。密钥直接从指定目录加载，演练目录只记录凭证路径。离线对账命令为 `python -m examples.two_node_demo verify reports/tls-demo`。

节点可运行 `python -m examples.peer_node NODE_B register https://b.example/node https://peer-b.example:8443`，用本机 node.json 中的节点名生成登记。通过 `review-public` 核对内容后，用 `share-public` 和返回的 SHA-256 发布该记录，引导节点运行 `python -m examples.peer_node NODE_DIRECTORY import-registration B https://b.example/node https://peer-b.example:8443` 仅取回该条登记，核对节点名及地址后写入本地公开库。目录运营者审核并共享后，其他节点即可发现它。更换地址后重新运行登记命令，更新过的记录需要再次审核共享。

配置跨主机连接时，在 node.json 中设定 `"transport":"mtls"`、本机接口的具体 IPv4 地址 `listen_address`，并在 `allowed_origins` 数组中逐项填写获准连接的 HTTPS 来源（如 `https://peer-b.example:8443`，含端口，无末尾斜线）。目录来源也需要列入。节点证书必须覆盖实际连接的主机名或 IP，同时保留节点名 URI。未配置时沿用回环监听；实际跨机器路由及防火墙条件仍待部署验收。

分端对账时，在 B 运行 `python -m examples.peer_node NODE_B statement A B-for-A.json`，通过双方认可的渠道将新建文件交给 A，再在 A 运行 `python -m examples.peer_node NODE_A reconcile B B-for-A.json`。文件只列双方交易的任务编号、服务方、测试费用与完整回执的 SHA-256 摘要。待处理交易的摘要为 null。核对输出匹配数量和逐笔差异；缺单、未结算和回执不一致都会导致非零退出。该文件用于核对两份记录，来源真实性依赖交付渠道。

双方启用双向 TLS 后，B 的操作者可在 node.json 中设置 `"statement_partners":["A"]` 并重启服务，允许证书名为 A 的节点查询 A 与 B 的账目摘要。A 完成发现后运行 `python -m examples.peer_node NODE_A reconcile-online B`。命令核对 B 的证书名，并按本地回执报告差异。默认共享名单为空，查询权限与会诊次数额度分别设置。

在线对账每页最多传输 256 笔摘要。接收端检查各页来自同一账本修订号；双方之间的任务记录在分页期间发生写入时，本次核对会失败，重新运行命令即可从最新账本开始。修订标记按合作双方独立生成，其他合作方的交易不会改变本次标记。离线导出仍生成完整的双方清单。

库主可以先用 `python -m open_roundtable.data NODE_DIR review-public SOURCE` 查看本地公开库中的正文、共享状态和 SHA-256，再用 `share-public SOURCE --if-sha256 HASH` 只发布已核对的内容。若期间导入了新版正文，发布操作会失败，需重新核对。使用 `share-public SOURCE --off` 可直接撤回当前共享。

私有库可按来源进一步收紧维护权限。运行 `python -m open_roundtable.data NODE_DIR record-limit SOURCE sealed`，该来源今后的更新也只在库内处理。选 `local --model MODEL` 可指定本机模型；整库切换为 sealed 时，这些记录也停止调用模型。用 `inherit` 恢复跟随整库设置。已有数据库在首次打开私有库时自动支持该命令。

运行 `python -m open_roundtable.data NODE_DIR preview-private SOURCE` 可在维护前查看整库权限、该来源的限制、实际生效方式、模型和目的服务地址。预览只显示地址的协议与主机（含显式端口），无需读取正文或调用模型。实际维护会在执行时重新读取并锁定权限。

### 按能力发现候选议员

提供方在本机运行 `python -m examples.peer_node NODE_B offer https://b.example/offers/repair repair "修复每周排班"`，审核后用 `python -m open_roundtable.data NODE_B share-public https://b.example/offers/repair --if-sha256 HASH` 发布。已经发现 B 的节点 A 运行 `python -m examples.peer_node NODE_A find-capability repair` 即可按标签查询。查询直接读取各节点最新公开目录，同时返回未能查询的节点。结果表示服务方当前公开声明的能力；实际受理仍取决于授权与剩余额度。

使用 `match-capability "自然语言需求"` 可让本机模型从当前公开声明中推荐候选。查询只返回候选，不发起会诊。运行 `python -m examples.capability_comparison` 可复现三组小样本比较：精确标签在同义修复能力上漏选一次；qwen3.5:9b 找到了两个修复候选，但在“只检查不改班”上漏选一项、误选两项。两组在无供给需求上均返回空。三次模型调用共用 405 个输入和 26 个输出 tokens。每组只运行一次，尚未检验接单成功率。

同一命令也比较预设允许标签后的选择。修复允许 repair、reschedule，只检查允许 audit，工资需求允许 payroll。仅按允许标签筛选，三例均无漏选或误选，模型调用为零。再让模型从允许范围中选，结果相同，修复消耗 111 个输入和 10 个输出 tokens，只检查消耗 83 和 7；工资需求没有候选，跳过调用。允许标签由实验预先给出，这组结果衡量已知操作范围内再调用模型的收益。

运行 python -m examples.capability_execution 可比较两种选择在九张本地合成班表（501–509）上的执行结果。候选为一个排班修复工具和一个只读检查工具。规则直接使用给定操作标签，模型每种需求选择一次，九张表复用选择。两组均修复成功 9/9，逐表改动 3、4、4、6、8、8、19、19、19 班；只读检查均与独立评分器一致，改动为零。模型共用 2 次调用、213 个输入和 14 个输出 tokens。实验覆盖本地执行，跨节点接单仍需另行验证。

发布服务时可重复指定 --rule。例如，match-capability 复核休息和连续工时 --allow-tag audit --require-rule R --require-rule L 只让模型评估同时声明 R 和 L 的服务。没有符合全部规则的候选时，模型调用和 tokens 均为零。运行 python -m examples.rule_discovery 可复现九张表的目录筛选对照。

自然语言查询支持重复指定 `--allow-tag`。例如 `match-capability "只检查排班冲突，保持班次不变" --allow-tag audit` 会先按公开声明的标签缩小候选，再让本机模型判断相关性。没有符合标签的声明时返回空并跳过推理。标签由提供方声明，执行效果仍需独立验证。

候选节点启用双向 TLS 后，可运行 `python -m examples.peer_node NODE_A admission B` 查询 B 此刻是否允许 A 发起新会诊。请求只包含发送节点名；B 按客户端证书、当前授权和余量回答。查询不占用名额；发送议题时仍会原子地复核并扣减额度。

双向 TLS 模式下，`find-capability` 和 `match-capability` 均支持 `--accepting-only`。查询会按节点核对当前受理资格，在推荐前筛除未授权或额度已用尽的节点；结果用 declined 列出明确拒绝的节点，用 unavailable 列出无法取得目录或资格答复的节点。筛选后为空时跳过模型调用。

发现结果中的 source 和 sha256 可传给 `send RECIPIENT TOPIC_SOURCE --offer OFFER_SOURCE SHA256`。首次发送前会复查对方公开目录；接收方受理新任务时也会复查。该引用纳入请求编号，恢复待处理请求时会沿用原引用。相同议题若改用另一份声明会形成独立任务，可能另行扣费。

运行 `python -m examples.two_node_demo run reports/offer-replay --bind-offer --fault before-receipt` 可复现绑定声明的断线恢复。B 在会诊结果落盘后退出，演练撤回该能力声明，再启动 B 并重发原请求。离线核验检查初次发现的声明摘要、两次投递的请求编号、恢复的费用以及最终公开目录。

2026-11-23 在同机两个节点进程、回环 HTTP 和本机 qwen3.5:9b 上复跑上述绑定声明演练。接收方退出后撤回声明，原请求重发成功；两次投递对应专家一次、模型一次、合计 2 个测试 credits。模型回执为输入 290、输出 7 tokens，议题状态为 RESOLVED。独立 verify 得到相同结果，受查记录中的私库标记命中数为零。双向 TLS 的同类回归使用可计数模型替身，跨物理主机仍待验收。

使用本机已安装的 OpenSSL 和 Ollama 可直接复跑加密演练：`python -m examples.two_node_demo run reports/real-tls-offer --temporary-tls openssl --bind-offer --fault before-receipt`。该模式为 A、B 和目录临时签发测试证书，退出后清理密钥。2026-11-23 同机双向 TLS 与真实 qwen3.5:9b 联合实测：两次投递、专家与模型各一次、2 credits、290 输入和 7 输出 tokens，离线核验一致。演练结束后两端配置指向的临时凭证目录均已清理。

能力查询和发送前的声明复核优先读取对端的 `/offers`，其中只包含该节点明确共享的能力声明。旧节点返回 404 时会兼容读取其完整公开目录；其他错误按查询失败处理。

在上述命令后加 `--with-synonym` 可加入同一修复工具的第二条公开声明。2026-11-23 本机复测中，qwen3.5:9b 对修复需求选中两条修复声明，合并为一次工具执行后成功 9/9；对只读需求也选中了这两条，漏选检查声明，执行前拦截，完成 0/9、改动零班。已知操作标签的规则两项均完成 9/9。三声明组两次模型选择共用 273 个输入和 20 个输出 tokens。每种需求只选择一次并在九表复用，因此只读组的九次拦截源于同一次误选。该实验未经过 credits 账本。

输出中的 `literal` 与模型读取相同的英文需求和公开声明，按共同单词数选取最高分，保留并列项；`rule` 则事先获得正确操作标签。两声明目录中，词匹配给修复需求选了检查工具，给检查需求同时选了两个工具，两项均被拦截；模型两项选对（213 输入、14 输出 tokens）。三声明目录中，词匹配两项均混选，模型只选对修复（273 输入、20 输出 tokens）。每组模型调用两次，词匹配零次。此对照仅覆盖两个固定英文句子，词匹配未做词形归一、停用词过滤或否定理解。

运行 `python -m examples.capability_requests` 可按九条独立英文需求复测三声明目录。修复、只读检查、目录无相应服务各三条；逐条输出预期和实际选中的声明、漏选、误选及模型消耗。2026-11-23 实测，词匹配完全命中 1/9，累计漏选 6 条、误选 5 条；本机 qwen3.5:9b 完全命中 8/9，漏选 1 条、误选 0 条，9 次调用合计 1,235 输入和 66 输出 tokens。唯一漏选发生在修复需求，仍找到一个可用修复声明。每句仅跑一次，此处衡量公开目录检索，不执行排班或记账。

节点的 `account` 输出用 `pending_jobs` 列出尚无回执的议题编号、服务提供方和使用方，可据此关联双方账单。若 `reconcile` 对同一编号报告 `pending at both nodes`，该议题仍待核对；账本中的零扣费仅表示尚未结算，无法据此判断模型是否已执行。

运行 `python -m examples.statement_benchmark` 比较待结算账目的分页读取，加 `--settled` 可比较已结算账目。实验自带提交 `23d8fbe` 的逐行筛选基线，在源码快照中也可运行。每种状态分别生成 10,000 笔合成议题，两种查询在同一账本中逐页读取并比较完整结果，每页 256 条。下表为本机预热后五轮耗时的中位数，单位为毫秒。

| 合作方占比 | 待结算逐行筛选 | 待结算索引 | 已结算逐行筛选 | 已结算索引 |
|---|---:|---:|---:|---:|
| 100% | 78.78 | 74.84 | 130.67 | 129.59 |
| 10% | 31.42 | 7.97 | 47.31 | 13.99 |
| 1% | 27.62 | 1.04 | 36.77 | 1.38 |

已结算组逐笔校验回执身份和费用，并计算回执摘要。此处只计本地账单读取，建索引、写入和网络传输耗时另计。

节点的 `send --offer` 当前执行排班修复。新请求绑定的公开声明须使用 `repair` 或 `reschedule` 标签；发送方在投递前、接收方在受理新任务时均检查所选声明的具体版本。节点可以同时发布只读检查等其他声明供发现。已受理任务按原有回执恢复。

运行 `find-capability repair reschedule` 可在一次目录查询中列出任一修复标签的声明；每个匹配项包含实际标签、发布节点和声明摘要。单独查询只读检查仍可用 `find-capability audit`。多标签查询支持与 `--accepting-only` 组合。
### 远端排班审查

提供方可用 `offer SOURCE audit "检查休息与工时" --rule R --rule L` 登记能力并共享该记录。发起方显式共享审查议题，例如 `{"operation":"schedule-audit-v1","rules":["R","L"],"shifts":["Axv:22-26","Bxu:29-33"]}`。用 `find-capability audit --require-rule R --require-rule L` 获取服务来源和摘要，再用 `send NODE TOPIC --offer SOURCE SHA256` 派单。回执中的 R 项会报告 `x@26-29`。此入口接受一周以内的合成班表编码，提供方每次新审查使用一个授权名额，规则工具扣费为零。

运行 `python -m unittest tests.test_peer_audit` 可复现同机双进程的九表审查、独立评分和重复派单核账。

运行 `python -m examples.rule_discovery --dispatch` 可用一个发起方和四个提供方在本机比较两种发现方式。对每张公开合成班表，两组都向全部入选服务尝试派单，并逐份与独立评分器核对。九张表中，仅按标签筛选产生 36 次尝试，其中 21 次在发送班表前被服务声明检查拦下。要求全部规则匹配产生 15 次尝试，全部取得正确回执。两组各有 36 次初始目录请求，之后发起方的网络请求分别为 51 次和 30 次，两组各登记 15 份回执、0 credits。
在双向 TLS 模式下，运行 `python -m examples.peer_node NODE_DIR discover BOOTSTRAP_URL --directory-name directory` 可要求引导服务的证书标明节点名 `directory`。名称不匹配时终止此次发现，保留上次同步的节点名单。双节点会诊演练的 TLS 模式为 A、B 两端启用了这项校验。

在提交 `7c76a55` 上，两端核对目录名后，使用本机 `qwen3.5:9b` 完成了一次合成排班会诊。运行 `python -m examples.two_node_demo run reports/named-directory-new --temporary-tls openssl --bind-offer --fault before-receipt` 可在新目录复现。接收端在内核回执落盘后中断，服务声明撤回后重发成功。两次投递合计调用模型一次、专家一次，扣费 2 credits，模型用量为 290 个输入和 7 个输出 tokens，议题结案。关闭模型后运行 `python -m examples.two_node_demo verify reports/named-directory-new` 得到相同结果，受检通信记录中私库标记命中数为零。
在提交 `f575721` 上，将同一演练的故障参数改为 `--fault after-commit`，并使用新的输出目录。接收方已记入 2 credits、发送方尚未收到回执时中断。撤下服务声明并重启后，两个节点对账一致。使用本机 `qwen3.5:9b` 实测，两次投递共调用模型一次、专家一次，使用 290 个输入和 7 个输出 tokens，议题结案。关闭模型后离线核验得到相同结果，`private_stores_checked` 列出 A、B 和 directory，三方预置标记在受检记录中均无命中。此项演练使用同机回环网络和合成排班。
