#!/usr/bin/env python3
"""Per-repo signal collection. Read-only: every call here is a GET."""

import base64
import re
from datetime import datetime, timedelta, timezone

README_RE = re.compile(r"^readme(\.|$)", re.I)
TEST_RE = re.compile(
    r"(^|/)tests?/|(^|/)spec/|_test\.[a-z]+$|\.test\.[a-z]+$|(^|/)test_[^/]+\.py$"
    r"|_spec\.[a-z]+$|(^|/)conftest\.py$|\.spec\.[a-z]+$",
    re.I,
)
CI_PATH_RE = re.compile(r"^\.github/workflows/.+\.ya?ml$", re.I)
DOC_RE = re.compile(r"\.(md|rst|adoc)$", re.I)

# Directories that hold code someone else wrote — never counted as the
# participant's own work.
VENDOR_RE = re.compile(
    r"(^|/)(node_modules|vendor|third_party|dist|build|out|target|\.venv|venv|"
    r"site-packages|__pycache__|\.next|\.nuxt|coverage|migrations/versions)/", re.I)
LOCKFILE_RE = re.compile(
    r"(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|"
    r"Gemfile\.lock|Cargo\.lock|composer\.lock|go\.sum)$", re.I)
MINIFIED_RE = re.compile(r"\.min\.(js|css)$|\.bundle\.js$", re.I)

# Average bytes per line, used to estimate line counts from blob sizes without
# fetching every file. Rough by design — see reference/signals.md.
BYTES_PER_LINE = {
    "py": 32, "js": 34, "jsx": 34, "ts": 34, "tsx": 34, "mjs": 34, "cjs": 34,
    "java": 33, "kt": 32, "scala": 34, "go": 30, "rb": 28, "rs": 32, "php": 32,
    "c": 30, "h": 28, "cpp": 32, "hpp": 30, "cs": 33, "swift": 32, "m": 30,
    "sh": 30, "bash": 30, "zsh": 30, "ps1": 32, "sql": 30, "r": 30,
    "html": 40, "css": 28, "scss": 28, "less": 28, "vue": 34, "svelte": 34,
    "tf": 30, "dockerfile": 30, "makefile": 26,
}
DEFAULT_BYTES_PER_LINE = 32


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


TS_OFFSET_RE = re.compile(r"([+-])(\d{2}):?(\d{2})$")
TS_FRACTION_RE = re.compile(r"\.\d+$")
TS_FORMATS = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d")


def parse_ts(value):
    """Parse a GitHub timestamp or a --since/--until argument into aware UTC.

    Hand-rolled rather than leaning on the ISO parsing helper added in Python
    3.7, or on %z accepting a colon in the offset (also 3.7), so this runs on
    3.6 as well.
    """
    if not value:
        return None
    text = value.strip()
    offset_minutes = 0
    match = TS_OFFSET_RE.search(text)
    if match and "T" in text:
        sign = 1 if match.group(1) == "+" else -1
        offset_minutes = sign * (int(match.group(2)) * 60 + int(match.group(3)))
        text = text[:match.start()]
    if text.endswith("Z"):
        text = text[:-1]
    text = TS_FRACTION_RE.sub("", text)
    for fmt in TS_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed - timedelta(minutes=offset_minutes) if offset_minutes else parsed
    return None


def collect_repo(gh, full_name, since, until, max_commit_details=40, loc_mode="estimate"):
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
    out["loc"] = _loc(gh, base, out["tree"], loc_mode)
    return out


def _loc(gh, base, tree, mode):
    info = {"mode": mode, "lines": None, "exact": False,
            "code_files": tree.get("code_file_count", 0), "partial": False}
    if mode == "off" or not tree.get("available"):
        return info
    if mode == "exact":
        lines, counted, skipped = exact_loc(gh, base, tree.get("code_files") or [])
        info.update({"lines": lines, "exact": True, "counted_files": counted,
                     "partial": bool(skipped) or bool(tree.get("truncated"))})
        return info
    info["lines"] = tree.get("loc_estimate", 0)
    info["partial"] = bool(tree.get("truncated"))
    return info


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


def _ext_of(path):
    name = path.rsplit("/", 1)[-1].lower()
    if name in ("makefile", "dockerfile"):
        return name
    return name.rsplit(".", 1)[-1] if "." in name else ""


def _is_code(path):
    if VENDOR_RE.search(path) or LOCKFILE_RE.search(path) or MINIFIED_RE.search(path):
        return False
    return _ext_of(path) in BYTES_PER_LINE


def _tree(gh, base, branch):
    info = {
        "file_count": 0, "has_readme": False, "test_paths": [], "has_tests": False,
        "has_claude_md": False, "claude_dir_paths": [], "workflow_paths": [],
        "truncated": False, "available": False,
        "readme_bytes": 0, "doc_count": 0, "top_level_dirs": [],
        "code_files": [], "code_file_count": 0, "loc_estimate": 0,
    }
    tree = gh.get(base + "/git/trees/" + branch, {"recursive": "1"}, accept_missing=True)
    if not tree or "tree" not in tree:
        return info
    info["available"] = True
    info["truncated"] = bool(tree.get("truncated"))
    blobs = [n for n in tree["tree"] if n.get("type") == "blob"]
    info["file_count"] = len(blobs)
    dirs, est = set(), 0.0
    for node in blobs:
        path = node["path"]
        size = node.get("size") or 0
        if "/" in path:
            dirs.add(path.split("/", 1)[0])
        if "/" not in path and README_RE.match(path):
            info["has_readme"] = True
            info["readme_bytes"] = size
        if TEST_RE.search(path) and not VENDOR_RE.search(path):
            info["test_paths"].append(path)
        if path == "CLAUDE.md" or path.endswith("/CLAUDE.md"):
            info["has_claude_md"] = True
        if path.startswith(".claude/"):
            info["claude_dir_paths"].append(path)
        if CI_PATH_RE.match(path):
            info["workflow_paths"].append(path)
        if DOC_RE.search(path) and not VENDOR_RE.search(path) and not README_RE.match(path):
            info["doc_count"] += 1
        if _is_code(path):
            info["code_files"].append({"path": path, "sha": node.get("sha"), "size": size})
            est += size / float(BYTES_PER_LINE.get(_ext_of(path), DEFAULT_BYTES_PER_LINE))
    info["has_tests"] = bool(info["test_paths"])
    info["test_paths"] = info["test_paths"][:8]
    info["top_level_dirs"] = sorted(d for d in dirs if not d.startswith("."))
    info["code_file_count"] = len(info["code_files"])
    info["loc_estimate"] = int(round(est))
    return info


def exact_loc(gh, base, code_files, max_files=250, max_bytes=400000):
    """Count real lines by fetching blobs. Returns (lines, counted, skipped)."""
    counted, skipped, total = 0, 0, 0
    for entry in code_files:
        if counted >= max_files:
            skipped += 1
            continue
        if (entry.get("size") or 0) > max_bytes or not entry.get("sha"):
            skipped += 1
            continue
        blob = gh.get(base + "/git/blobs/" + entry["sha"], accept_missing=True)
        if not blob or blob.get("encoding") != "base64":
            skipped += 1
            continue
        try:
            raw = base64.b64decode(blob.get("content") or "")
            text = raw.decode("utf-8", "replace")
        except (ValueError, TypeError):
            skipped += 1
            continue
        if "\x00" in text[:2000]:  # binary that slipped through the extension filter
            skipped += 1
            continue
        total += text.count("\n") + (1 if text and not text.endswith("\n") else 0)
        counted += 1
    return total, counted, skipped


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
