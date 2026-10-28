#!/usr/bin/env python3
"""Measure how fast and how well a headless Claude Code agent answers questions about herd-device-logs.

  python3 bench.py smoke  --ref acad791
  python3 bench.py run    --label before --ref acad791
  python3 bench.py run    --label after  --ref WORKTREE
  python3 bench.py report before after

Each label gets a clean copy of the repo (git archive of --ref, or the working tree's tracked and untracked
non-ignored files for WORKTREE), outside this folder, so the agent never sees the answer key.
"""
import argparse
import concurrent.futures
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
DEFAULT_REPO = Path("/Users/dmea/Documents/herd-device-logs")

SMOKE_PROMPT = (
    "Without using any tools, answer from your instructions only, one line each:\n"
    "1. Quote the first heading of every CLAUDE.md file in your context, or say none.\n"
    "2. Do your instructions mention Kubernetes, Teleport, Grafana or an internship? yes or no.\n"
    "3. What is the command to build and test everything in this repo?"
)


def snapshot(repo: Path, ref: str, label: str) -> Path:
    # resolve(): macOS reports /var/folders as /private/var/folders in tool calls
    dest = Path(tempfile.mkdtemp(prefix=f"herd-bench-{label}-")).resolve()
    if ref == "WORKTREE":
        files = subprocess.run(["git", "-C", str(repo), "ls-files", "-co", "--exclude-standard", "-z"],
                               check=True, capture_output=True).stdout.split(b"\0")
        for f in filter(None, files):
            src = repo / f.decode()
            if src.is_file():
                (dest / f.decode()).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest / f.decode())
    else:
        archive = subprocess.run(["git", "-C", str(repo), "archive", ref], check=True, capture_output=True).stdout
        subprocess.run(["tar", "-x", "-C", str(dest)], input=archive, check=True)
    return dest


def clean_env() -> dict:
    # Drop the parent Claude Code session's variables so each run starts like a fresh terminal launch.
    return {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}


def claude_cmd(prompt: str, args, tools: str) -> list:
    return ["claude", "-p", prompt,
            "--model", args.model, "--effort", args.effort,
            "--tools", tools,
            "--setting-sources", "project",
            "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
            "--disable-slash-commands",
            "--no-session-persistence",
            "--output-format", "stream-json", "--verbose"]


def parse_stream(lines: list, root: Path) -> dict:
    tools, files, result, init = {}, [], {}, {}
    for line in lines:
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            init = ev
        elif ev.get("type") == "assistant":
            for block in ev.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    tools[block["name"]] = tools.get(block["name"], 0) + 1
                    path = block.get("input", {}).get("file_path")
                    if path:
                        files.append(os.path.relpath(path, root) if path.startswith(str(root)) else path)
        elif ev.get("type") == "result":
            result = ev
    usage = result.get("usage", {})
    return {
        "answer": result.get("result", ""),
        "is_error": result.get("is_error", True),
        "duration_ms": result.get("duration_ms"),
        "duration_api_ms": result.get("duration_api_ms"),
        "num_turns": result.get("num_turns"),
        "cost_usd": result.get("total_cost_usd"),
        "input_tokens": sum(usage.get(k, 0) for k in
                            ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")),
        "output_tokens": usage.get("output_tokens", 0),
        "tool_calls": sum(tools.values()),
        "tools": tools,
        "files_read": sorted(set(files)),
        "init_model": init.get("model"),
        "init_mcp_servers": init.get("mcp_servers"),
    }


def score(answer: str, expect: list) -> tuple:
    hits = [any(re.search(p, answer, re.IGNORECASE) for p in alts) for alts in expect]
    return sum(hits), len(hits), [alts[0] for alts, hit in zip(expect, hits) if not hit]


def run_one(q: dict, rep: int, root: Path, suffix: str, args, raw_dir: Path) -> dict:
    start = time.monotonic()
    proc = subprocess.run(claude_cmd(q["question"] + suffix, args, "Read,Grep,Glob"), cwd=root, env=clean_env(),
                          capture_output=True, text=True, timeout=args.timeout)
    wall_ms = int((time.monotonic() - start) * 1000)
    (raw_dir / f"{q['id']}-r{rep}.jsonl").write_text(proc.stdout)
    row = parse_stream(proc.stdout.splitlines(), root)
    got, total, missing = score(row["answer"], q["expect"])
    row.update({"id": q["id"], "rep": rep, "wall_ms": wall_ms, "exit_code": proc.returncode,
                "score": got, "max_score": total, "passed": got == total, "missing": missing,
                "stderr": proc.stderr[-2000:]})
    return row


def cmd_smoke(args):
    root = snapshot(args.repo, args.ref, "smoke")
    proc = subprocess.run(claude_cmd(SMOKE_PROMPT, args, ""), cwd=root, env=clean_env(),
                          capture_output=True, text=True, timeout=args.timeout)
    row = parse_stream(proc.stdout.splitlines(), root)
    print(f"snapshot: {root}\nexit: {proc.returncode}  model: {row['init_model']}  "
          f"mcp: {row['init_mcp_servers']}  input tokens: {row['input_tokens']}\n")
    print(row["answer"] or proc.stderr)


def cmd_run(args):
    spec = json.loads((HERE / args.questions).read_text())
    questions = [q for q in spec["questions"] if not args.only or q["id"].split("-")[0] in args.only.split(",")]
    RESULTS.mkdir(exist_ok=True)
    raw_dir = RESULTS / args.label
    raw_dir.mkdir(exist_ok=True)
    out = RESULTS / f"{args.label}.jsonl"
    root = snapshot(args.repo, args.ref, args.label)
    meta = {"meta": True, "label": args.label, "ref": args.ref, "model": args.model, "effort": args.effort,
            "reps": args.reps, "jobs": args.jobs, "snapshot": str(root), "started": time.strftime("%FT%T%z")}
    if args.ref != "WORKTREE":
        meta["commit"] = subprocess.run(["git", "-C", str(args.repo), "rev-parse", args.ref],
                                        capture_output=True, text=True).stdout.strip()
    lock = threading.Lock()
    with out.open("w") as fh:
        fh.write(json.dumps(meta) + "\n")
        jobs = [(q, rep) for rep in range(1, args.reps + 1) for q in questions]
        print(f"{len(jobs)} runs, snapshot {root}", flush=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = {pool.submit(run_one, q, rep, root, spec["suffix"], args, raw_dir): (q, rep) for q, rep in jobs}
            for fut in concurrent.futures.as_completed(futures):
                q, rep = futures[fut]
                try:
                    row = fut.result()
                except Exception as e:  # a timeout or crash is still a data point
                    row = {"id": q["id"], "rep": rep, "passed": False, "error": repr(e)}
                with lock:
                    fh.write(json.dumps(row) + "\n")
                    fh.flush()
                print(f"  {row['id']} r{rep}: {'PASS' if row.get('passed') else 'FAIL'} "
                      f"{row.get('wall_ms', 0) / 1000:.1f}s {row.get('tool_calls', '?')} tools "
                      f"{row.get('missing') or row.get('error') or ''}", flush=True)
    shutil.rmtree(root, ignore_errors=True)
    print(f"results: {out}")


def load(label: str) -> tuple:
    rows = [json.loads(l) for l in (RESULTS / f"{label}.jsonl").read_text().splitlines() if l.strip()]
    return next((r for r in rows if r.get("meta")), {}), [r for r in rows if not r.get("meta")]


def summarise(rows: list) -> dict:
    def med(key):
        vals = [r[key] for r in rows if r.get(key) is not None]
        return statistics.median(vals) if vals else None
    return {"n": len(rows), "pass": sum(1 for r in rows if r.get("passed")),
            "wall_s": (med("wall_ms") or 0) / 1000, "turns": med("num_turns"), "tools": med("tool_calls"),
            "files": statistics.median([len(r.get("files_read", [])) for r in rows]) if rows else None,
            "in_tok": med("input_tokens"), "out_tok": med("output_tokens"),
            "cost": sum(r.get("cost_usd") or 0 for r in rows)}


def fmt(s: dict) -> str:
    return (f"{s['pass']}/{s['n']:<3} {s['wall_s']:6.1f}s {s['turns'] or 0:5.1f}t {s['tools'] or 0:5.1f}c "
            f"{s['files'] or 0:4.1f}f {int(s['in_tok'] or 0):>7}in ${s['cost']:.2f}")


def cmd_report(args):
    loaded = [(label, *load(label)) for label in args.labels]
    for label, meta, _ in loaded:
        print(f"{label}: ref={meta.get('ref')} {meta.get('commit', '')[:7]} model={meta.get('model')} "
              f"effort={meta.get('effort')} reps={meta.get('reps')} jobs={meta.get('jobs')}")
    print("\ncolumns: passed/runs, median wall time, turns, tool calls, distinct files read, input tokens, "
          "total cost\n")
    ids = sorted({r["id"] for _, _, rows in loaded for r in rows})
    print(f"{'question':<20}" + "".join(f"{label:<44}" for label in args.labels))
    for qid in ids:
        print(f"{qid:<20}" + "".join(f"{fmt(summarise([r for r in rows if r['id'] == qid])):<44}"
                                      for _, _, rows in loaded))
    totals = [summarise(rows) for _, _, rows in loaded]
    print(f"{'ALL':<20}" + "".join(f"{fmt(t):<44}" for t in totals))
    if len(totals) == 2 and totals[0]["wall_s"]:
        a, b = totals
        print(f"\nchange in median wall time: {100 * (b['wall_s'] - a['wall_s']) / a['wall_s']:+.0f}%, "
              f"tool calls: {(b['tools'] or 0) - (a['tools'] or 0):+.1f}, "
              f"pass rate: {a['pass']}/{a['n']} -> {b['pass']}/{b['n']}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("smoke", "run"):
        s = sub.add_parser(name)
        s.add_argument("--ref", required=True, help="git ref, or WORKTREE for the uncommitted working tree")
        s.add_argument("--repo", type=Path, default=DEFAULT_REPO)
        s.add_argument("--model", default="claude-opus-5-5")
        s.add_argument("--effort", default="high")
        s.add_argument("--timeout", type=int, default=900)
        if name == "run":
            s.add_argument("--label", required=True)
            s.add_argument("--reps", type=int, default=3)
            s.add_argument("--jobs", type=int, default=3, help="parallel runs; keep it the same for every label")
            s.add_argument("--only", help="comma-separated question numbers, e.g. q01,q04")
            s.add_argument("--questions", default="questions.json", help="question file next to bench.py")
    r = sub.add_parser("report")
    r.add_argument("labels", nargs="+")
    args = p.parse_args()
    {"smoke": cmd_smoke, "run": cmd_run, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
