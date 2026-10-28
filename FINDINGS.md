# Docs layout benchmark: acad791 vs f9ebbbb

This compares how a headless Claude Code agent answers questions about `herd-device-logs` before and after the docs
reorganisation in commit `f9ebbbb`. Two tests ran on the same two commits with the same settings:

- **Light test:** 14 lookup questions, each answered from one or two files.
- **Deep test:** 14 harder questions that need a reason, a number or a trade-off joined from several docs.

## Summary

**Light test: same accuracy at lower cost.** Every run on both commits answered correctly. The agent used 15% fewer
tool calls, 12% fewer input tokens and 15% less money. Wall time barely moved, because model latency makes up more
than 90% of it. In the new layout the agent went to the new `CONFIGURATION.md` files and the filled-in ADRs, where
before it had to read source and config files.

**Deep test: the layouts separate.** Out of 42 runs per commit, the regex scorer passed 21 before and 32 after. With a
rubric judge on the runs the regex could not be trusted for, the counts are 27 and 40. The agent used 15% fewer tool
calls and 20% less money. The main cause is that the facts are now in the repo. Before, the ADRs were stubs and the
research notes did not exist, and 13 judged answers said the repo does not say. After, only 1 did. So the deep result
is not a pure layout effect.

**Together:** the light test shows the new layout makes lookups cheaper. The deep test shows the moved content makes
harder questions answerable. The deep test cannot tell those two effects apart.

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

Both tests used the same settings:

| Setting | Value |
|---|---|
| Model | `claude-opus-5-5` |
| Effort | `high` |
| Tools | `Read`, `Grep`, `Glob` |
| Repetitions | 3 per question (42 runs per label per test) |
| Parallel jobs | 3 |
| Claude Code CLI | 2.1.285 |
| Light test started | `before` 2026-10-27 14:43 EDT, `after` 2026-10-28 10:45 EDT |
| Deep test started | `deep-before` 2026-10-28 14:16 EDT, `deep-after` 2026-10-28 14:23 EDT |

Before the `after` run, `python3 bench.py smoke --ref f9ebbbb` confirmed the isolation. Only the root `CLAUDE.md` was
loaded, no user-level instructions reached the agent, and the MCP server list was empty.

## How the benchmark works

The project has five files and a results folder:

```
herd-agent-bench/
├── bench.py              # the benchmark (smoke, run, report, judge)
├── questions.json        # light test: 14 questions, the expected answers, and a prompt suffix
├── questions-deep.json   # deep test: 14 questions with expected answers, notes and rubrics
├── README.md             # how to run it
├── FINDINGS.md           # this file
└── results/
    ├── before.jsonl      # light test: one summary row per run, for acad791
    ├── before/           # light test: raw agent output, one file per run
    ├── after.jsonl       # light test: one summary row per run, for f9ebbbb
    ├── after/            # light test: raw agent output, one file per run
    ├── deep-before.jsonl, deep-before/, deep-after.jsonl, deep-after/   # the same, for the deep test
    ├── deep-before.judge.jsonl, deep-after.judge.jsonl                  # judge verdicts (see "Answer judge")
    └── judge-v1/, judge-v2/                                             # earlier judge runs, kept as evidence
```

The benchmark lives outside the `herd-device-logs` repo on purpose. The agent must never see the question files,
because they hold the answer keys.

### questions.json and questions-deep.json

Each file has two keys. `suffix` is appended to every question: "Answer from this repository only. Be brief, and list
the file paths you used." `questions` is a list of 14 entries. Each entry has an `id`, the `question` text and an
`expect` list. Each item in `expect` is a list of alternative regexes, and an answer matches an item if any of its
alternatives matches. For example, `q12-credentials` expects `[["IRSA"], ["Pod Identity"]]`.

Entries in `questions-deep.json` also have a `note` (where the answer lives), a `rubric` (3 to 5 claims for the
judge) and, on three questions, `"always_judge": true`. `bench.py` ignores `note`.

### bench.py

`bench.py` has four subcommands.

`smoke --ref <ref>` runs one prompt with no tools. The prompt asks the agent to quote the headings of the `CLAUDE.md`
files in its context, and to say whether it was told anything about Kubernetes, Teleport, Grafana or an internship.
Those topics appear only in the user's own `~/.claude` instructions. A "no" means user-level config did not leak in.

`run --label <name> --ref <ref>` is the benchmark itself. `--questions <file>` picks the question file and defaults to
`questions.json`.

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

`judge <label>...` grades some runs against the question's rubric. It is described under "Answer judge".

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
fields. The `deep-*` files have the same shape.

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
| `is_error`, `exit_code`, `stderr` | Error information. All 168 runs of both tests exited cleanly |
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

## Light test: 14 lookup questions

Each question asks for one fact that sits in one or two files.

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

### Caveats

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

## Deep test: 14 reasoning questions

The light test gave the same accuracy on both layouts (41 and 42 of 42), so accuracy could not show a difference.
`questions-deep.json` adds 14 harder questions, built from the design doc
(`polycom_log_service_implementation_architecture`, last updated 19 Oct 2026) and checked against the repo's ADRs and
`docs/research/`. They ran on the same two commits with the same settings, as labels `deep-before` and `deep-after`.

| Kind | Questions |
|---|---|
| A reason that spans an ADR and a research note | q15 never 503, q18 rate-limit evidence, q23 enable `calls.xml`, q25 key collision |
| A change and what else it breaks | q16 raise the S3 timeout, q20 MAC moves customer then redelivery, q24 urgent prod fix |
| A path across both services | q17 launch-day burst, q21 forged headers, q26 source IP from request to row |
| A value that must be worked out, not quoted | q19 customer lookup down for 30 minutes (about 5 minutes to the DLQ) |
| Something the docs do not settle | q22 unverified ALB behaviour, q27 production retention, q28 customer endpoint |

### Result

| | before | after | Change |
|---|---|---|---|
| Passed, regex only | 21 / 42 | 32 / 42 | +11 |
| Passed, regex plus judge | 27 / 42 | 40 / 42 (41 after the false alarm below) | +13 |
| Judged answers that say "the repo does not say" | 13 | 1 | -12 |
| Tool calls | 350 | 297 | -15% |
| Agent cost | $7.18 | $5.73 | -20% |

The deep set separates the layouts where the light set could not. Regex and judge agree on the direction, and the
judge only moves the pass counts up.

| Question | before regex / judge | after regex / judge |
|---|---|---|
| q15-never-503 | 0 / 0 | 3 / 3 |
| q16-raise-s3-timeout | 3 / 3 | 3 / 3 |
| q17-launch-burst | 0 / 0 | 0 / 3 |
| q18-rate-limit-evidence | 0 / 0 | 3 / 3 |
| q19-lookup-outage | 1 / 3 | 2 / 3 |
| q20-customer-move | 3 / 3 | 3 / 3 |
| q21-forged-headers | 2 / 3 | 0 / 2 (3 on reading) |
| q22-unverified-alb | 0 / 0 | 3 / 3 |
| q23-enable-calls-xml | 3 / 3 | 3 / 3 |
| q24-urgent-fix | 3 / 3 | 3 / 3 |
| q25-key-collision | 3 / 3 | 2 / 2 |
| q26-source-ip-trace | 1 / 3 | 2 / 3 |
| q27-retention | 0 / 0 | 2 / 3 |
| q28-customer-endpoint | 2 / 3 | 3 / 3 |

Passes out of 3 runs per question.

### What drove the difference

Where the layouts differ, the cause is that the facts are now in the repo. Before, ADRs 0009, 0010 and 0004 were
stubs that pointed at pages of a design doc that is not in the repo, and `docs/research/` did not exist. On q15, q17,
q18, q22 and q27 the agent said so, and correctly gave up. After, it found the reason in the ADR and the number in
`docs/research/`. So this test measures that facts moved into the repo, not only that files moved, which the light
test did not. Do not read the +13 as a pure layout effect.

The agents were more accurate than expected about stubs. In both layouts they reported that `networkpolicy.yaml` holds
only a comment and that the customer lookup is a stub that answers "unassigned". My first rubric for q21 got this wrong
and marked those answers as errors.

### Answer judge

The regex scorer cannot tell a correct answer in different words from a wrong one, and it cannot tell an honest "not
documented" from an invented value. `python3 bench.py judge <labels> --questions questions-deep.json` adds a rubric
check, and it runs only where the regex cannot be trusted:

- the run failed the regex, on a question that has a `rubric` (it may be a false failure), or
- the question has `"always_judge": true` (q19, q27, q28), where a regex pass cannot rule out an invented or wrongly
  derived answer.

Runs that pass the regex on any other question are never judged. That was 41 of the 84 deep runs (24 before, 17
after). The judge is `claude-sonnet-5-5` at effort `medium`, a different model from the one that answered. It sees
only the question, the answer and 3 to 5 rubric claims, and gives each claim `met`, `not_stated` or `contradicted`,
plus whether the answer admits a gap. The run then gets one outcome:

| Outcome | Meaning |
|---|---|
| `correct` | Every claim met |
| `honest_gap` | Claims missing, and the answer says the repo does not contain them |
| `incomplete` | Claims missing, with no admission |
| `wrong` | A claim contradicted. Read it, do not count it (see below) |

Results go to `results/<label>.judge.jsonl`. A full judge pass costs about $0.56 for both labels. The regex rows in
`results/<label>.jsonl` are never changed.

**What the judge found**

- **The regex produced 14 false failures in 84 runs** (6 before, 8 after), all rescued by the judge. Typical cases:
  q19 wrote "has to leave the message on the queue" where the regex wanted "never ... unassigned"; q26 wrote "adds the
  address it saw to the end" where the regex wanted "append"; q28 wrote "hasn't been confirmed" where the regex wanted
  "not confirmed". One was my fault: the q17 key demands `550`, which the question does not ask for, so all 3 `after`
  answers failed on a correct answer.
- **No false passes.** Every regex pass that the judge re-checked on q19, q27 and q28 was judged `correct`.
- **The honest-gap count is the clearest layout signal**: 13 judged `before` answers admit a gap and 1 `after` answer does.
  The regex records these as plain failures, with no way to tell them from wrong answers.

**How far to trust the judge**

- **`correct` and `honest_gap` held up. `wrong` did not.** The first judge run flagged 7 answers as `wrong`. I read all
  7 and none was wrong: 3 were my rubric (q21, above) and 4 were the judge treating "the repo can't tell" as the
  opposite of "it is safe". I tightened the prompt, and the final run still flagged 3 (q18 before r1 and r3, q21 after
  r3). I read those too and all 3 were false alarms. Treat `wrong` as "go and read this one". `adjusted` counts it as a
  fail, which understates the pass rate. The one verified false alarm (q21 after r3) is why `after` reads 40 and 41.
- **Rerun noise.** Two runs with the same prompt and the same answers gave the same outcome on 37 of 41 rows. Every
  difference was on a borderline row (q18, q21, q25).
- **Hand check.** I labelled 11 judged answers myself against the rubric. The judge agreed on correct versus not
  correct on 10, and gave the same exact outcome on 7. The misses were the three false `wrong` flags and one
  `honest_gap` I would call `incomplete`. This is too few rows to compute an agreement statistic such as Cohen's kappa.
- **One parsing bug**, fixed: the judge sometimes writes prose after its JSON, which broke `json.loads` on one row.
  The parser now reads only the first JSON object.

### Caveats

The light-test caveats on repetitions, wall time and `files_read` apply here too. The two deep runs started 7 minutes
apart, so the day-to-day load caveat does not. In addition:

- One judge model, one prompt, and an 11-row hand check. Expect a few rows to flip on a rerun.
- The rubrics and answer keys were checked against the repo HEAD (`644027e`, which merges `develop` after `f9ebbbb`),
  while `deep-after` ran on `f9ebbbb`. The `after` answers matched, but a claim could differ in a way I did not see.
- q19 is an inference (5 receives times a 60 s visibility timeout). The judge accepts any figure consistent with that,
  which is looser than the regex.
- The regexes in `questions-deep.json` were left as they were when the runs were scored, including the q17 `550` and
  the strict q21 and q25 patterns. Fixing them now would make the stored scores disagree with the file.

## What the two tests say together

| | Light test | Deep test |
|---|---|---|
| What it asks | One fact in one or two files | A reason, number or trade-off joined from several docs |
| Accuracy | Same on both layouts | Better after (27 to 40 of 42 with the judge) |
| Cost | -15% | -20% |
| Tool calls | -15% | -15% |
| Why | The agent reads a module `CLAUDE.md` or `CONFIGURATION.md` in place of source and config | The facts and their reasons now exist in the repo |
| Scorer | Regex was enough | Regex under-counts, so a judge covers the doubtful runs |

The layout change helps the agent find things sooner. The content change is what lets it answer the harder
questions at all. To separate the two, run the deep test on a commit that has the new content in the old layout, which
does not exist yet.

## Suggested fixes to the benchmark

Not done yet, except where marked.

1. Count the `path` of `Grep` calls that use `output_mode: "content"` in `files_read`.
2. Loosen the `q06-unknown-mac` regex so that up to a few words may sit between the verb and "on the queue".
3. Fix the column spacing in `report`. The cost column runs into the next label's pass count, so `$0.11` followed by
   `3/3` prints as `$0.113/3`. The `fmt()` string is longer than the 44-character column it is padded to.
4. Score answer quality beyond keyword matches for questions like q12, where a longer answer can be better. Done for
   the deep test with `bench.py judge`. The light test has no rubrics yet, so q12 is still regex-only.
5. Before relying on the judge for a decision, label 30 or more answers by hand and compute agreement. 11 is a
   spot-check.
6. Remove `550` from the q17 key and loosen q21 and q25, then rerun, so the regex and the judge stop disagreeing.

## How to reproduce

From `herd-agent-bench/`:

```bash
python3 bench.py smoke --ref f9ebbbb
python3 bench.py run --label before --ref acad791
python3 bench.py run --label after --ref f9ebbbb19ad04feb5f28243548d62297bec931f1
python3 bench.py report before after
```

A full light run takes about 5 minutes with 3 parallel jobs and costs $3 to $4.

The deep test and its judge:

```bash
python3 bench.py run --label deep-before --ref acad791 --questions questions-deep.json
python3 bench.py run --label deep-after  --ref f9ebbbb --questions questions-deep.json
python3 bench.py judge deep-before deep-after --questions questions-deep.json   # about $0.30 per label
python3 bench.py judge deep-before deep-after --questions questions-deep.json --summary-only   # reprint, no calls
```

A full deep run costs $5 to $7. `results/judge-v1/` and `results/judge-v2/` hold the earlier judge runs described under
"How far to trust the judge".
