# herd-agent-bench

Measures how quickly and how accurately a headless Claude Code agent answers 14 questions about `herd-device-logs`,
so a change to the repo's docs layout can be compared with the layout before it.

Kept outside the repo on purpose: the agent works in a clean copy and must never see `questions.json`.

## Run

```bash
python3 bench.py smoke --ref acad791                       # check isolation: root CLAUDE.md loaded, nothing from ~/.claude
python3 bench.py run --label before --ref acad791          # baseline, 14 questions x 3 reps
python3 bench.py run --label after --ref WORKTREE          # uncommitted changes in the repo (or a commit/branch name)
python3 bench.py report before after
```

Defaults: `claude-opus-5-5`, effort `high`, tools `Read,Grep,Glob`, 3 runs in parallel. Keep `--model`, `--effort` and
`--jobs` the same for every label you compare. One full run is about 42 x $0.09.

## Isolation

Each run uses `--setting-sources project`, an empty `--strict-mcp-config`, `--disable-slash-commands` and
`--no-session-persistence`, and drops every `CLAUDE*` environment variable. So it loads the repo's CLAUDE.md files but
not `~/.claude/CLAUDE.md`, user rules, plugins, hooks or MCP servers.

## Output

- `results/<label>.jsonl`: one metadata line, then one line per run (answer, pass, missing items, wall time, turns,
  tool calls by name, files read, tokens, cost).
- `results/<label>/<question>-r<n>.jsonl`: the raw stream-json of each run, to see exactly where the agent looked.

A run passes only if its answer matches every item in that question's `expect` list (case-insensitive regex, any
alternative in an item counts). A failure can mean a wrong answer or a too-strict regex: read the answer before
trusting it.
