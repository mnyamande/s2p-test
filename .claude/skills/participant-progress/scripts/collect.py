#!/usr/bin/env python3
"""Per-repo signal collection. Read-only: every call here is a GET."""

import re
from datetime import datetime, timedelta, timezone

README_RE = re.compile(r"^readme(\.|$)", re.I)
TEST_RE = re.compile(
    r"(^|/)tests?/|(^|/)spec/|_test\.[a-z]+$|\.test\.[a-z]+$|(^|/)test_[^/]+\.py$"
    r"|_spec\.[a-z]+$|(^|/)conftest\.py$|\.spec\.[a-z]+$",
    re.I,
)
CI_PATH_RE = re.compile(r"^\.github/workflows/.+\.ya?ml$", re.I)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None


def collect_repo(gh, full_name, since, until, max_commit_details=40):
    """Gather every raw signal for one repo. Never raises for a single bad repo."""
    owner, name = full_name.split("/", 1)
    base = "/repos/%s/%s" % (owner, name)
    out = {"full_name": full_name, "error": None}

    meta = gh.get(base, accept_missing=True)
    if not meta:
        out["error"] = "repo not accessible (missing, or the token lacks read access)"
        return out

    out.update({
        "html_url": meta.get("html_url"),
        "default_branch": meta.get("default_branch") or "main",
        "private": meta.get("private"),
        "archived": meta.get("archived"),
        "fork": meta.get("fork"),
        "created_at": meta.get("created_at"),
        "pushed_at": meta.get("pushed_at"),
        "size_kb": meta.get("size"),
        "description": meta.get("description"),
    })

    out["commits"] = _commits(gh, base, since, until, max_commit_details)
    out["prev_commit_count"] = _prev_commit_count(gh, base, since, until)
    out["recent_history"] = _recent_history(gh, base)
    out["tree"] = _tree(gh, base, out["default_branch"])
    out["actions"] = _actions(gh, base, since)
    out["claude_md"] = _claude_md_history(gh, base, out["tree"])
    return out


def _commits(gh, base, since, until, max_details):
    raw = gh.paginate(
        base + "/commits",
        {"since": iso(since), "until": iso(until)},
        max_items=300,
        accept_missing=True,
    ) or []
    commits = []
    for item in raw:
        c = item.get("commit", {}) or {}
        commits.append({
            "sha": item.get("sha"),
            "message": (c.get("message") or "").strip(),
            "author_login": (item.get("author") or {}).get("login"),
            "author_name": (c.get("author") or {}).get("name"),
            "author_date": (c.get("author") or {}).get("date"),
            "committer_date": (c.get("committer") or {}).get("date"),
            "parents": len(item.get("parents") or []),
            "additions": None,
            "deletions": None,
            "files_changed": None,
        })
    # Commit size needs a per-commit call; cap it so a busy cohort stays cheap.
    for commit in commits[:max_details]:
        detail = gh.get(base + "/commits/" + commit["sha"], accept_missing=True)
        if not detail:
            continue
        stats = detail.get("stats") or {}
        commit["additions"] = stats.get("additions")
        commit["deletions"] = stats.get("deletions")
        commit["files_changed"] = len(detail.get("files") or [])
    return commits


def _prev_commit_count(gh, base, since, until):
    """Commit count in the equally-sized window immediately before this one."""
    span = until - since
    prev_since = since - span
    raw = gh.paginate(
        base + "/commits",
        {"since": iso(prev_since), "until": iso(since)},
        max_items=300,
        accept_missing=True,
    ) or []
    return len(raw)


def _recent_history(gh, base):
    """Latest commit regardless of window, so a stalled repo still has a date."""
    raw = gh.paginate(base + "/commits", max_items=100, accept_missing=True) or []
    info = {"sampled_count": len(raw), "last_date": None, "last_sha": None,
            "last_message": None, "authors": {}}
    if raw:
        top = raw[0]
        c = top.get("commit", {}) or {}
        info["last_date"] = ((c.get("committer") or {}).get("date")
                             or (c.get("author") or {}).get("date"))
        info["last_sha"] = top.get("sha")
        info["last_message"] = (c.get("message") or "").strip().splitlines()[0][:120]
    for item in raw:
        login = (item.get("author") or {}).get("login")
        if login:
            info["authors"][login] = info["authors"].get(login, 0) + 1
    return info


def _tree(gh, base, branch):
    info = {
        "file_count": 0, "has_readme": False, "test_paths": [], "has_tests": False,
        "has_claude_md": False, "claude_dir_paths": [], "workflow_paths": [],
        "truncated": False, "available": False,
    }
    tree = gh.get(base + "/git/trees/" + branch, {"recursive": "1"}, accept_missing=True)
    if not tree or "tree" not in tree:
        return info
    info["available"] = True
    info["truncated"] = bool(tree.get("truncated"))
    paths = [n["path"] for n in tree["tree"] if n.get("type") == "blob"]
    info["file_count"] = len(paths)
    for path in paths:
        if "/" not in path and README_RE.match(path):
            info["has_readme"] = True
        if TEST_RE.search(path):
            info["test_paths"].append(path)
        if path == "CLAUDE.md" or path.endswith("/CLAUDE.md"):
            info["has_claude_md"] = True
        if path.startswith(".claude/"):
            info["claude_dir_paths"].append(path)
        if CI_PATH_RE.match(path):
            info["workflow_paths"].append(path)
    info["has_tests"] = bool(info["test_paths"])
    info["test_paths"] = info["test_paths"][:8]
    return info


def _actions(gh, base, since):
    out = {"configured": False, "runs": [], "unavailable": False}
    runs = gh.paginate(
        base + "/actions/runs", {"per_page": 50}, max_items=50, accept_missing=True
    )
    if runs is None:
        out["unavailable"] = True
        return out
    if not runs:
        return out
    out["configured"] = True
    for run in runs:
        out["runs"].append({
            "name": run.get("name"),
            "head_sha": run.get("head_sha"),
            "status": run.get("status"),
            "conclusion": run.get("conclusion"),
            "created_at": run.get("created_at"),
            "html_url": run.get("html_url"),
            "event": run.get("event"),
        })
    return out


def _claude_md_history(gh, base, tree):
    """Bonus signal: does a CLAUDE.md exist, and is it being revised over time?"""
    info = {"present": bool(tree.get("has_claude_md")),
            "commit_count": 0, "first_seen": None, "last_updated": None,
            "committed_claude_dir": bool(tree.get("claude_dir_paths")),
            "claude_dir_sample": (tree.get("claude_dir_paths") or [])[:5]}
    if not info["present"]:
        return info
    hist = gh.paginate(
        base + "/commits", {"path": "CLAUDE.md"}, max_items=50, accept_missing=True
    ) or []
    info["commit_count"] = len(hist)
    if hist:
        info["last_updated"] = ((hist[0].get("commit") or {}).get("author") or {}).get("date")
        info["first_seen"] = ((hist[-1].get("commit") or {}).get("author") or {}).get("date")
    return info
