#!/usr/bin/env python3
"""
sync_dashboard.py — scan parallax_tracer commits in saras_kno_B and emit progress.json.

Run locally:
    python scripts/sync_dashboard.py --source-root /path/to/knowledge_base

Run in CI (uses GITHUB_TOKEN / GH_PAT env vars):
    python scripts/sync_dashboard.py --source-repo shravan-surya/saras_kno_B --ci

The script:
  1. Clones (or reads) saras_kno_B
  2. Walks git log for knowledge_base/plugins/parallax_tracer/
  3. Maps commits → milestones via file-pattern & keyword rules
  4. Writes progress.json next to this script's repo root
"""
from __future__ import annotations
import argparse, json, os, re, subprocess, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path

PARALLAX_SUBPATH = "knowledge_base/plugins/parallax_tracer"

# ── Milestone rules ─────────────────────────────────────────────────────────
# Each rule: id must match index.html DEFAULT_MILESTONES ids.
# files[]    : regex matched against any file path in the commit diff
# keywords[] : matched (OR, case-insensitive) against commit message
RULES = [
    {
        "id": 1, "title": "Concept & Architecture Design",
        "files":    [r"CONCEPT", r"concept\.md", r"plugin\.yaml"],
        "keywords": ["concept", "brainstorm", "architecture", "hackathon concept"],
    },
    {
        "id": 2, "title": "Plugin Skeleton, DB Schema & REST Routes",
        "files":    [r"001_initial", r"routes/", r"tab_registry_ext",
                     r"bridge_resolver", r"parsers/__init__", r"proto_parser",
                     r"plugin\.yaml", r"importers/__init__"],
        "keywords": ["plugin skeleton", "phase-42", "portal nav", "skeleton"],
    },
    {
        "id": 3, "title": "Signal Parsers — All 5 Layers",
        "files":    [r"rvm_parser", r"ral_bus_parser", r"routing_rules_parser",
                     r"app_code_parser", r"orchestrator_parser",
                     r"002_expanded_schema", r"test_parsers", r"scanner\.py"],
        "keywords": ["scaffolding", "parser", "all 5", "day 1+2"],
    },
    {
        "id": 4, "title": "Signal Flow Graph Builder",
        "files":    [r"graph_builder"],
        "keywords": ["graph_builder", "graph builder", "flow_edge", "blast radius"],
    },
    {
        "id": 5, "title": "Git Provenance & Scan Tracking",
        "files":    [r"git_provenance", r"003_scan_provenance"],
        "keywords": ["provenance", "scan_run", "repo_ref", "file_manifest"],
    },
    {
        "id": 6, "title": "AI Diagram Narrator (Offline LLM)",
        # parallax_narrator.py + 008_c4_narrative migration
        "files":    [r"parallax_narrator", r"008_c4_narrative",
                     r"skills/parallax", r"parallax.*SKILL\.md",
                     r"parallax.*populator\.py", r"parallax.*skill_schema"],
        "keywords": ["narrator", "huggingface", "offline llm", "diagram narrator",
                     "skill.md", "populator", "skill contract", "parallax skill"],
    },
    {
        "id": 7, "title": "Live Signal Mining on Firmware Codebase",
        "files":    [r"plantuml_renderer", r"auto_scan"],
        "keywords": ["auto-scan", "staleness", "staleness check", "auto-scan endpoint",
                     "auto scan", "firmware scan", "jar output"],
    },
    {
        "id": 8, "title": "MCP Tool — parallax_trace()",
        "files":    [r"mcp_tool", r"mcp_parallax", r"parallax_mcp"],
        "keywords": ["parallax_trace()", "mcp tool", "mcp integration", "mcp parallax",
                     "kb_parallax_trace", "kb_parallax_signals", "kb_parallax_bridge",
                     "mcp tools", "/query api", "query api"],
    },
    {
        "id": 9, "title": "Cytoscape Visualization & Final Demo",
        "files":    [r"c4_generator"],
        "keywords": ["parallax-portal", "interactive graph", "blast diagram", "c4 plantuml",
                     "plantuml", "parallax-46b", "cytoscape", "tool panels", "js panels"],
    },
    # ── Stretch goals (ids 10-12) ─────────────────────────────────────────
    {
        "id": 10, "title": "Full G1+G2+G3 — App→RVM→Elpis→Zonal 4-Layer Chain",
        "files":    [r"004_zonal_nodes", r"005_dedup_and_edge_types", r"006_g2b_bridge"],
        "keywords": ["G1+G2+G3", "full app", "elpis", "zonal chain",
                     "4-layer", "four-layer", "complete chain"],
    },
    {
        "id": 11, "title": "Bidirectional BFS Signal Search + Diagram Store",
        "files":    [r"diagram_store", r"007_c4_diagrams_store"],
        "keywords": ["bidirectional bfs", "bidirectional", "bfs", "diagram store",
                     "source/diagram toggle"],
    },
    {
        "id": 12, "title": "What-If Blast Radius + Pan/Zoom Diagram Modal",
        "files":    [],
        "keywords": ["what-if", "blast radius", "pan/zoom", "pan-zoom",
                     "diagram modal", "svg rendering"],
    },
    {
        "id": 13, "title": "fcose Physics Layout + ECU Cluster View + Box Select",
        "files":    [r"cytoscape-fcose", r"cose-base", r"layout-base"],
        "keywords": ["fcose", "fcose layout", "ecu cluster", "cluster view",
                     "box-select", "box select", "shuffle button"],
    },
]


def sh(cmd: list[str], cwd: str | None = None, check=True) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if check and r.returncode != 0:
        print(f"[warn] command failed: {' '.join(cmd)}\n{r.stderr[:200]}", file=sys.stderr)
    return r.stdout.strip()


def clone_source(repo: str, token: str | None) -> str:
    """Clone saras_kno_B into a temp dir and return its path."""
    tmp = tempfile.mkdtemp(prefix="parallax_source_")
    url = f"https://github.com/{repo}.git"
    if token:
        url = f"https://{token}@github.com/{repo}.git"
    print(f"  Cloning {repo}…")
    subprocess.run(["git", "clone", "--filter=blob:none", url, tmp], check=True,
                   capture_output=False)
    return tmp


def get_commits(source_root: str) -> list[dict]:
    """Return all commits touching PARALLAX_SUBPATH, newest first."""
    raw = sh(["git", "log", "--format=%H|%aI|%s", "--",
               PARALLAX_SUBPATH], cwd=source_root)
    commits = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = line.split("|", 2)
        if len(parts) < 3:
            continue
        sha, date, msg = parts
        files = sh(["git", "diff-tree", "--no-commit-id", "-r", "--name-only", sha],
                   cwd=source_root).splitlines()
        files = [f for f in files if PARALLAX_SUBPATH in f]
        commits.append({"sha": sha[:8], "full_sha": sha, "date": date,
                        "message": msg.strip(), "files": files})
    return commits


def classify(commit: dict) -> list[int]:
    msg = commit["message"].lower()
    all_files = "\n".join(commit["files"])
    matched = []
    for rule in RULES:
        hit = (any(re.search(p, all_files) for p in rule["files"]) or
               any(kw in msg for kw in rule["keywords"]))
        if hit:
            matched.append(rule["id"])
    return matched


def build_progress(commits: list[dict], branch: str) -> dict:
    triggered: dict[int, dict] = {}
    for c in reversed(commits):        # oldest first → keep earliest date per milestone
        for ms_id in classify(c):
            if ms_id not in triggered:
                triggered[ms_id] = c

    updates = []
    for ms_id, c in sorted(triggered.items()):
        updates.append({
            "id": ms_id,
            "status": "done",
            "completed_at": c["date"],
            "source_commit": c["sha"],
            "source_message": c["message"],
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_repo":  "shravan-surya/saras_kno_B",
        "branch":       branch,
        "commit_count": len(commits),
        "commits": [{"sha": c["sha"], "date": c["date"], "message": c["message"]}
                    for c in commits],
        "milestone_updates": updates,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source-root",   default=None,
                    help="Local path to knowledge_base git root")
    ap.add_argument("--source-repo",   default="shravan-surya/saras_kno_B",
                    help="GitHub repo to clone if --source-root not given")
    ap.add_argument("--output",        default="progress.json",
                    help="Output path (default: progress.json)")
    ap.add_argument("--ci",            action="store_true",
                    help="CI mode: clone source-repo using GITHUB_TOKEN / GH_PAT")
    args = ap.parse_args()

    token = os.environ.get("GH_PAT") or os.environ.get("GITHUB_TOKEN")

    # Resolve source root
    if args.source_root:
        source_root = str(Path(args.source_root).resolve())
        branch = sh(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=source_root,
                    check=False) or "unknown"
        cleanup = None
    elif args.ci or not args.source_root:
        source_root = clone_source(args.source_repo, token)
        branch = "feature/parallax_tracer"
        cleanup = source_root
    else:
        ap.error("Provide --source-root or --ci")

    print(f"Scanning {source_root}/{PARALLAX_SUBPATH} …")
    commits = get_commits(source_root)
    print(f"  {len(commits)} commits found")

    progress = build_progress(commits, branch)
    done_ids = [u["id"] for u in progress["milestone_updates"]]
    print(f"  Milestones detected as done: {done_ids}")
    for u in progress["milestone_updates"]:
        print(f"    #{u['id']}  {u['source_commit']}  {u['source_message'][:65]}")

    out = Path(args.output)
    out.write_text(json.dumps(progress, indent=2))
    print(f"\n✓ Written: {out.resolve()}")

    if cleanup:
        import shutil
        shutil.rmtree(cleanup, ignore_errors=True)


if __name__ == "__main__":
    main()
