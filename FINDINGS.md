# Docs layout benchmark: acad791 vs f9ebbbb

This compares how a headless Claude Code agent answers 14 questions about `herd-device-logs` before and after the
docs reorganisation in commit `f9ebbbb`.

## Summary

The new docs layout gave the same accuracy at lower cost. Every run on both commits answered correctly. The agent
used 15% fewer tool calls, 12% fewer input tokens and 15% less money. Wall time barely moved, because model latency
makes up more than 90% of it. In the new layout the agent went to the new `CONFIGURATION.md` files and the filled-in
ADRs, where before it had to read source and config files.

## Commits tested

| Label | Commit | Author | Date | Message |
|---|---|---|---|---|
| `before` | `acad791d69f4add5a4ce5f49399be4269f3c7314` | Jovani | 2026-10-27 | Add Envoy as the ALB stand-in for a real phone (#40) |
| `after` | `f9ebbbb19ad04feb5f28243548d62297bec931f1` | dmea | 2026-10-28 | Give each kind of doc one home and fill in the ADRs |

`acad791` is the direct parent of `f9ebbbb`, so the only difference between the two snapshots is the docs change. That
commit touches 48 files (972 lines added, 382 removed). The main changes are:

- Each ADR in `docs/architecture/decisions/` gets a Modules line and its source. Each module's `CLAUDE.md` lists the
  ADRs that apply to it. The new `check-docs.sh` fails when these lists, the ADR references or the README table drift
  apart.
- Settings move into a new `CONFIGURATION.md` for each service (`upload-service/` and `processor/`).
- The measurements the decisions rely on move to `docs/research/`. The load-test and dashboard notes move under
  `docs/`.
- `upload-service/ARCHITECTURE.md` and `processor/ARCHITECTURE.md` gain about 80 lines each.
- The two `src/test*/README.md` files in `upload-service` are removed.

Both runs used the same settings:

| Setting | Value |
|---|---|
| Model | `claude-opus-5-5` |
| Effort | `high` |
| Tools | `Read`, `Grep`, `Glob` |
| Repetitions | 3 per question (42 runs per label) |
| Parallel jobs | 3 |
| Claude Code CLI | 2.1.285 |
| Started | `before` 2026-10-27 14:43 EDT, `after` 2026-10-28 10:45 EDT |

Before the `after` run, `python3 bench.py smoke --ref f9ebbbb` confirmed the isolation. Only the root `CLAUDE.md` was
loaded, no user-level instructions reached the agent, and the MCP server list was empty.

## How the benchmark works

The project has three files and a results folder:

```
herd-agent-bench/
├── bench.py          # the benchmark (smoke, run, report)
├── questions.json    # 14 questions, the expected answers, and a prompt suffix
├── README.md         # how to run it
├── FINDINGS.md       # this file
└── results/
    ├── before.jsonl  # one summary row per run, for acad791
    ├── before/       # raw agent output, one file per run
    ├── after.jsonl   # one summary row per run, for f9ebbbb
    └── after/        # raw agent output, one file per run
```

The benchmark lives outside the `herd-device-logs` repo on purpose. The agent must never see `questions.json`,
because it holds the answer key.

### questions.json

The file has two keys. `suffix` is appended to every question: "Answer from this repository only. Be brief, and list
the file paths you used." `questions` is a list of 14 entries. Each entry has an `id`, the `question` text and an
`expect` list. Each item in `expect` is a list of alternative regexes, and an answer matches an item if any of its
alternatives matches. For example, `q12-credentials` expects `[["IRSA"], ["Pod Identity"]]`.

| id | Question (shortened) |
|---|---|
| q01-build | Commands to build and test the whole repo, and only the upload service |
| q02-new-rejection | Files to change to add a new upload rejection reason |
| q03-storage-status | HTTP status when the S3 write fails or times out, and which status it must never return |
| q04-timeouts | The S3 write, ALB idle and Tomcat keep-alive timeouts, and the ADR that sets them |
| q05-s3-key | Format of the S3 object key |
| q06-unknown-mac | What the processor does with an unknown MAC, and when the customer lookup fails |
| q07-processor-ports | The processor's app and management ports when run locally |
| q08-compose-env | The docker-compose variable that overrides the AWS endpoint, and the naming mistake to avoid |
| q09-new-file-type | Steps and files to accept a new uploaded file type |
| q10-dlq | Whether the processor may send to the DLQ itself, and which ADR covers it |
| q11-calls-xml | Why `calls.xml` is rejected by default, and its HTTP status |
| q12-credentials | How the processor gets AWS credentials, compared with the upload service |
| q13-single-it | How to run only `S3ObjectMetadataReaderIT` |
| q14-aws-imports | Which processor packages may import AWS SDK classes |

### bench.py

`bench.py` has three subcommands.

`smoke --ref <ref>` runs one prompt with no tools. The prompt asks the agent to quote the headings of the `CLAUDE.md`
files in its context, and to say whether it was told anything about Kubernetes, Teleport, Grafana or an internship.
Those topics appear only in the user's own `~/.claude` instructions. A "no" means user-level config did not leak in.

`run --label <name> --ref <ref>` is the benchmark itself:

1. `snapshot()` makes a clean copy of the repo in a new temp folder. For a commit it pipes `git archive <ref>` into
   `tar`. For `--ref WORKTREE` it copies the tracked and untracked files that git does not ignore. The agent works in
   this copy, never in the real repo.
2. It writes a metadata line to `results/<label>.jsonl` with the label, ref, resolved commit hash, model, effort,
   reps, jobs, snapshot path and start time.
3. It builds the list of runs (every question times every repetition) and sends them to a thread pool of `--jobs`
   workers.
4. `run_one()` starts `claude -p "<question><suffix>"` in the snapshot folder and saves its stdout to
   `results/<label>/<id>-r<rep>.jsonl`. It then parses the output and scores the answer.
5. Each result is written to `results/<label>.jsonl` as soon as it finishes, so a crash keeps the runs already done.
   A timeout or exception still becomes a row with `passed: false` and the error.
6. When all runs finish, it deletes the snapshot.

`report <label> <label>...` loads each label's file and prints a table per question. The table shows passes, median
wall time, turns, tool calls and files read, plus median input tokens and total cost.

The other functions:

- `claude_cmd()` builds the CLI command. The flags `--setting-sources project`, `--strict-mcp-config` with an empty
  server list, `--disable-slash-commands` and `--no-session-persistence` keep out everything except the repo's own
  `CLAUDE.md` files. The output format is `stream-json` with `--verbose`, so every event is recorded.
- `clean_env()` drops every environment variable that starts with `CLAUDE`, so a run started from inside a Claude
  Code session behaves like one started from a fresh terminal.
- `parse_stream()` reads the stream-json events. It counts tool calls by name and collects the `file_path` of each
  call. It takes the answer, turns, duration, cost and token usage from the final `result` event. Input tokens are
  the sum of uncached, cache-creation and cache-read tokens.
- `score()` checks each `expect` item against the answer, case-insensitively. A run passes only if every item
  matches.

### What is in before.jsonl and after.jsonl

Each file has 43 lines. The first is the metadata line described above. The other 42 are one row per run, with these
fields:

| Field | Meaning |
|---|---|
| `id`, `rep` | Question id and repetition number (1 to 3) |
| `answer` | The agent's final answer text |
| `passed`, `score`, `max_score`, `missing` | Scoring result. `missing` lists the first regex of each item that did not match |
| `wall_ms` | Time measured by `bench.py` around the CLI process |
| `duration_ms`, `duration_api_ms` | Total time and model API time, as reported by Claude Code |
| `num_turns` | Agent turns |
| `tool_calls`, `tools` | Total tool calls, and calls by tool name |
| `files_read` | Distinct paths passed as `file_path`, relative to the snapshot |
| `input_tokens`, `output_tokens` | Token usage |
| `cost_usd` | Cost of the run |
| `is_error`, `exit_code`, `stderr` | Error information. All 84 runs exited cleanly |
| `init_model`, `init_mcp_servers` | Model and MCP servers reported at startup, to confirm isolation |

### What is in before/ and after/

Each folder holds 42 files named `<question id>-r<rep>.jsonl`, such as `q01-build-r1.jsonl`. `before/` takes 1.8 MB
and `after/` takes 1.4 MB. Each file is the raw stream-json output of one run, one JSON event per line, in this order:

1. A `system` event with subtype `init`. It records the snapshot path (`cwd`), the tools offered, the model, the MCP
   servers (empty), the CLI version and the plugins that loaded.
2. `system` events with subtype `thinking_tokens`.
3. `assistant` events. Their content holds the agent's thinking, its text and its `tool_use` blocks, each with the
   tool name and input (pattern, path, glob, output mode).
4. `user` events holding the `tool_result` for each tool call, which is what the agent saw.
5. A `rate_limit_event`.
6. A final `result` event with the answer, turns, durations, usage and total cost.

Read these files to see where the agent looked and in what order. The summary rows lose that detail. This script
prints the tool calls of one run:

```bash
python3 -c "
import json, sys
for line in open(sys.argv[1]):
    e = json.loads(line)
    if e.get('type') == 'assistant':
        for c in e['message']['content']:
            if c.get('type') == 'tool_use':
                print(c['name'], json.dumps(c['input']))
" results/after/q07-processor-ports-r1.jsonl
```

## Findings

### Totals over 42 runs

| | before | after | Change |
|---|---|---|---|
| Passed | 41 (really 42, see below) | 42 | none |
| Tool calls | 191 | 162 | -15% |
| Turns | 233 | 204 | -12% |
| Input tokens | 1.62M | 1.42M | -12% |
| Output tokens | 50.9k | 44.7k | -12% |
| Total cost | $3.76 | $3.18 | -15% |
| Mean wall time | 16.7 s | 15.6 s | -6% |
| 90th percentile wall time | 25.8 s | 24.3 s | -6% |
| Read / Grep / Glob calls | 94 / 67 / 30 | 77 / 62 / 23 | Read -18% |

The one `before` failure is a scoring mistake, not a wrong answer. In `q06-unknown-mac` r1 the agent wrote
"`QueuePoller` leaves the message on the queue". The expected regex allows only "it" between the verb and "on the
queue", so "the message" broke the match. Both commits got all 42 answers right.

### Per question

Medians over three runs, except cost, which is the total for the three runs.

| Question | Tool calls | Wall time (s) | Cost |
|---|---|---|---|
| q01-build | 2 / 1 | 9.1 / 7.3 | $0.11 / $0.07 |
| q02-new-rejection | 9 / 4 | 25.5 / 16.6 | $0.38 / $0.20 |
| q03-storage-status | 3 / 4 | 13.5 / 15.9 | $0.35 / $0.28 |
| q04-timeouts | 3 / 3 | 13.3 / 12.5 | $0.20 / $0.20 |
| q05-s3-key | 3 / 4 | 12.9 / 12.7 | $0.21 / $0.20 |
| q06-unknown-mac | 9 / 8 | 23.5 / 24.3 | $0.42 / $0.33 |
| q07-processor-ports | 5 / 1 | 12.2 / 9.0 | $0.19 / $0.08 |
| q08-compose-env | 3 / 2 | 12.1 / 9.3 | $0.34 / $0.22 |
| q09-new-file-type | 9 / 11 | 34.0 / 34.0 | $0.51 / $0.49 |
| q10-dlq | 2 / 2 | 10.6 / 11.2 | $0.14 / $0.11 |
| q11-calls-xml | 3 / 3 | 15.7 / 18.0 | $0.22 / $0.29 |
| q12-credentials | 4 / 5 | 17.8 / 20.7 | $0.29 / $0.41 |
| q13-single-it | 5 / 4 | 15.9 / 13.8 | $0.20 / $0.14 |
| q14-aws-imports | 4 / 2 | 14.8 / 11.1 | $0.19 / $0.15 |

Each cell is `before / after`. 11 of the 14 questions got cheaper, one cost the same and two cost more.

### Where the agent looked

The agent found the new docs and used them in place of code and config.

- q04-timeouts. Before, all three runs read `upload-service/src/main/resources/application.yml`. After, two of the
  three read `upload-service/CONFIGURATION.md` instead.
- q12-credentials. All three runs read `processor/CONFIGURATION.md`, where before they read `processor/CLAUDE.md` and
  the Java config classes `S3StorageConfig` and `S3ClientConfig`.
- q03-storage-status. Two of the three runs read ADR 0009, where before every run read `RejectionStatusMapper.java`
  and `openapi.yaml`.
- q05-s3-key. Every run read ADR 0011. Before, they read `docs/architecture/s3-layout.md`.

The largest savings came from the module `CLAUDE.md` files.

- q02-new-rejection. Before, every run read four files, including `LogUploadControllerTest.java`. After,
  `upload-service/CLAUDE.md` was enough in most runs, and tool calls fell from 9 to 4.
- q14-aws-imports. Before, two of the three runs also opened `processor/ARCHITECTURE.md`. After, `processor/CLAUDE.md`
  alone answered it.
- q07-processor-ports. Before, every run opened both processor `application*.yml` files. After, two targeted Grep
  calls answered it.

q09-new-file-type read the same five files in every run on both commits, so the new layout did not change it.

### q12-credentials costs more but answers better

q12 is the largest cost increase ($0.29 to $0.41). In the new layout every run reads three files:
`processor/CONFIGURATION.md`, the upload service's `serviceaccount.yaml` and ADR 0012. The answers are fuller. They
compare the two services in a table and explain, from ADR 0012, that the upload service was first going to use IRSA
too. On 19 October that changed to EKS Pod Identity to follow infra8's practice, and the processor stayed on IRSA. The scoring checks only for "IRSA" and "Pod
Identity", so it cannot see that difference.

## Caveats

- Three repetitions per question are few. A difference of one or two tool calls on a single question is within the
  noise. The total drop in tool calls, tokens and cost is more reliable, because it shows up in most questions and
  matches what the raw output shows the agent reading.
- Wall time is a poor measure here. Model API time was 91% of wall time in `before` and 93% in `after`, and the docs
  layout does not change model speed.
- `files_read` undercounts. `parse_stream()` records only the `file_path` input, which `Read` uses. A `Grep` with
  `output_mode: "content"` returns file text through its `path` input, so it is not counted. Several `after` runs
  show 0 files read even though they read content this way. q01-build ran one Grep inside `upload-service/CLAUDE.md`,
  and q07-processor-ports ran Greps over `processor/` and `local/`. Part of the drop in files read (median 2 to 1) is
  a move from Read to Grep, not less reading.
- The two runs were a day apart. Both used the same model and CLI version, but API load on each day may have
  affected wall time.

## Suggested fixes to the benchmark

These are not done yet.

1. Count the `path` of `Grep` calls that use `output_mode: "content"` in `files_read`.
2. Loosen the `q06-unknown-mac` regex so that up to a few words may sit between the verb and "on the queue".
3. Fix the column spacing in `report`. The cost column runs into the next label's pass count, so `$0.11` followed by
   `3/3` prints as `$0.113/3`. The `fmt()` string is longer than the 44-character column it is padded to.
4. Score answer quality beyond keyword matches for questions like q12, where a longer answer can be better.

## How to reproduce

From `herd-agent-bench/`:

```bash
python3 bench.py smoke --ref f9ebbbb
python3 bench.py run --label before --ref acad791
python3 bench.py run --label after --ref f9ebbbb19ad04feb5f28243548d62297bec931f1
python3 bench.py report before after
```

A full run takes about 5 minutes with 3 parallel jobs and costs $3 to $4.
