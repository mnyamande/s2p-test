#!/usr/bin/env python3
"""Scan a GitHub org's participant repos and write a facilitator briefing.

Read-only. Prefers the `gh` CLI when it is installed and authenticated, and
falls back to the REST API with GH_TOKEN / GITHUB_TOKEN.

    scan.py --org agentic-ai-coop --roster roster.csv --md report.md --html report.html

Repo -> participant mapping, in priority order:
  1. an explicit row in --roster (CSV or JSON), keyed by repo name
  2. --pattern, e.g. 's2p-{participant}' turns s2p-jane-smith into "Jane Smith"
  3. otherwise the repo name is used as-is and flagged as inferred in the report
"""

import sys

if sys.version_info < (3, 6):
    sys.stderr.write(
        "This script needs Python 3.6 or newer (found %d.%d). On macOS, "
        "/usr/bin/python3 is usually current:\n"
        "  /usr/bin/python3 %s ...\n" % (sys.version_info[0], sys.version_info[1],
                                         sys.argv[0] if sys.argv else "script.py"))
    raise SystemExit(1)

import argparse
import csv
import io
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from analyze import analyse_repo, order_by_urgency, summarise_participant  # noqa: E402
from collect import collect_repo, iso, parse_ts  # noqa: E402
from ghclient import GitHub, GitHubError  # noqa: E402
from render import to_html, to_markdown  # noqa: E402

DEFAULT_ORG = "agentic-ai-coop"
DEFAULT_PATTERN = "s2p-{participant}"
DEFAULT_WINDOW_DAYS = 14
MIN_WINDOW_DAYS = 7
MAX_WINDOW_DAYS = 30
SKIP_REPOS = {".github"}

NAME_COLS = ("participant", "name", "full name", "full_name", "fullname", "display name")
REPO_COLS = ("repo", "repository", "repo name", "repo_name", "slug")
HANDLE_COLS = ("handle", "github", "github handle", "github_handle", "username", "login", "gh")


# -- roster ----------------------------------------------------------------

def load_roster(path):
    """Return {repo_name_lower: {"participant":..., "handle":...}} plus handle-only rows."""
    if not path:
        return {}, {}
    if not os.path.exists(path):
        raise SystemExit("roster file not found: %s" % path)
    by_repo, by_handle = {}, {}
    if path.lower().endswith(".json"):
        with open(path) as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            # {"repo-name": "Real Name"} or {"handle": "Real Name"}
            for key, value in data.items():
                entry = value if isinstance(value, dict) else {"participant": value}
                target = by_repo if "-" in key or "/" in key else by_handle
                target[key.split("/")[-1].lower()] = {
                    "participant": entry.get("participant") or entry.get("name") or key,
                    "handle": entry.get("handle") or entry.get("github"),
                }
        else:
            for row in data:
                _absorb_row(row, by_repo, by_handle)
        return by_repo, by_handle

    with open(path, newline="") as fh:
        # Facilitators comment their rosters; drop # lines and blanks before parsing.
        lines = [ln for ln in fh.read().splitlines()
                 if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines:
        return by_repo, by_handle
    try:
        dialect = csv.Sniffer().sniff("\n".join(lines[:20]), delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    for row in csv.DictReader(io.StringIO("\n".join(lines)), dialect=dialect):
        _absorb_row(row, by_repo, by_handle)
    return by_repo, by_handle


def _absorb_row(row, by_repo, by_handle):
    clean = {(k or "").strip().lower(): (v or "").strip()
             for k, v in row.items() if k is not None}
    name = next((clean[c] for c in NAME_COLS if clean.get(c)), None)
    repo = next((clean[c] for c in REPO_COLS if clean.get(c)), None)
    handle = next((clean[c] for c in HANDLE_COLS if clean.get(c)), None)
    if not (name or handle):
        return
    record = {"participant": name or handle, "handle": handle}
    if repo:
        record["repo"] = repo.split("/")[-1]
        by_repo[record["repo"].lower()] = record
    if handle:
        by_handle[handle.lower()] = record


def repos_from_roster(path, org):
    """Repo list taken from a roster CSV/JSON's repo column."""
    by_repo, _ = load_roster(path)
    out = []
    for record in by_repo.values():
        name = record.get("repo")
        if name:
            out.append(name if "/" in name else "%s/%s" % (org, name))
    return sorted(set(out), key=str.lower)


def pattern_to_regex(pattern):
    parts = re.escape(pattern).split(re.escape("{participant}"))
    if len(parts) != 2:
        raise SystemExit("--pattern must contain exactly one {participant} placeholder")
    return re.compile("^%s(?P<participant>.+)%s$" % (parts[0], parts[1]), re.I)


def prettify(slug):
    words = re.split(r"[-_.\s]+", slug.strip())
    return " ".join(w[:1].upper() + w[1:] for w in words if w)


def map_repo(repo_name, roster_by_repo, pattern_re):
    key = repo_name.lower()
    if key in roster_by_repo:
        entry = roster_by_repo[key]
        return entry["participant"], entry.get("handle"), "roster"
    match = pattern_re.match(repo_name) if pattern_re else None
    if match:
        return prettify(match.group("participant")), None, "pattern"
    return repo_name, None, "inferred"


# -- repo discovery --------------------------------------------------------

BLOCKED_HINT = (
    "Listing every repo in '%s' was refused (%s).\n\n"
    "If you are running inside a Claude Code cloud session, this is expected: a "
    "cloud session's proxy only allows repo-scoped API paths for the repos "
    "attached to the session, so /orgs/<org>/repos is blocked no matter what "
    "your GitHub credentials can see. Either:\n"
    "  * run this skill from your own terminal, where `gh` is unrestricted, or\n"
    "  * pass the repos explicitly with --repos or --repos-from <file>.\n\n"
    "If you are running locally, check the org name and that `gh auth status` "
    "shows an account with read access to the org."
)


def list_org_repos(gh, org, include_forks=False, include_archived=False, exclude=None):
    try:
        repos = gh.paginate("/orgs/%s/repos" % org, {"type": "all", "sort": "pushed"})
    except GitHubError as exc:
        if exc.status == 404:
            # A personal account rather than an org — same shape, different endpoint.
            repos = gh.paginate("/users/%s/repos" % org, {"type": "owner", "sort": "pushed"},
                                accept_missing=True)
        elif exc.status in (403, 401):
            raise GitHubError(BLOCKED_HINT % (org, "HTTP %s" % exc.status), exc.status)
        else:
            raise
    if repos is None:
        raise GitHubError(
            "Could not list repos for '%s'. Check the org name and that your "
            "credentials have read access to it." % org)
    kept, skipped = [], 0
    for repo in repos:
        if repo["name"] in SKIP_REPOS:
            skipped += 1
            continue
        if exclude and exclude.search(repo["name"]):
            skipped += 1
            continue
        if repo.get("fork") and not include_forks:
            skipped += 1
            continue
        if repo.get("archived") and not include_archived:
            skipped += 1
            continue
        kept.append(repo["full_name"])
    return kept, skipped


# -- snapshots -------------------------------------------------------------

def snapshot_dir(data_dir, org):
    path = os.path.join(data_dir, re.sub(r"[^A-Za-z0-9_.-]", "_", org))
    os.makedirs(os.path.join(path, "runs"), exist_ok=True)
    return path


def load_snapshot(path):
    latest = os.path.join(path, "latest.json")
    if not os.path.exists(latest):
        return None
    try:
        with open(latest) as fh:
            return json.load(fh)
    except (ValueError, OSError):
        return None


def write_snapshot(path, report):
    payload = {
        "run_at": report["meta"]["generated_at"],
        "org": report["meta"]["org"],
        "since": report["meta"]["since"],
        "until": report["meta"]["until"],
        "repos": {},
        "participants": {},
    }
    for entry in report["participants"]:
        payload["participants"][entry["participant"]] = {
            "status": entry["status"], "score": entry["score"], "repos": entry["repos"]}
        for detail in entry["repo_details"]:
            metrics = detail.get("metrics") or {}
            payload["repos"][detail["repo"]] = {
                "status": entry["status"],
                "run_at": report["meta"]["generated_at"],
                "metrics": {
                    "last_commit_sha": metrics.get("last_commit_sha"),
                    "ci_fail_streak": metrics.get("ci_fail_streak"),
                    "commits": metrics.get("commits"),
                    "days_since_last_commit": metrics.get("days_since_last_commit"),
                },
            }
    latest = os.path.join(path, "latest.json")
    stamp = re.sub(r"[^0-9]", "", report["meta"]["generated_at"])[:14]
    with open(os.path.join(path, "runs", "%s.json" % stamp), "w") as fh:
        json.dump(payload, fh, indent=2)
    with open(latest, "w") as fh:
        json.dump(payload, fh, indent=2)
    return latest


# -- window ----------------------------------------------------------------

def resolve_window(args, snapshot):
    now = datetime.now(timezone.utc)
    until = parse_ts(args.until) if args.until else now
    if args.since:
        since = parse_ts(args.since)
        if not since:
            raise SystemExit("--since must look like 2026-08-06 or 2026-08-06T00:00:00Z")
        return since, until, None
    if args.days:
        return until - timedelta(days=args.days), until, None
    if snapshot and snapshot.get("run_at"):
        last = parse_ts(snapshot["run_at"])
        if last:
            days = (until - last).total_seconds() / 86400.0
            # Floor the window so trend/cadence math still has something to chew on.
            days = min(MAX_WINDOW_DAYS, max(MIN_WINDOW_DAYS, days))
            return until - timedelta(days=days), until, snapshot["run_at"]
    return until - timedelta(days=DEFAULT_WINDOW_DAYS), until, None


# -- main ------------------------------------------------------------------

def build_parser():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--org", default=DEFAULT_ORG, help="GitHub org (default: %s)" % DEFAULT_ORG)
    ap.add_argument("--repos", help="comma-separated owner/name list; skips org enumeration")
    ap.add_argument("--repos-from", dest="repos_from",
                    help="scan exactly these repos instead of enumerating the org. "
                         "Either a .csv/.json roster (its repo column is the list) or a "
                         "text file with one owner/name per line. Use this when org-wide "
                         "listing is blocked, e.g. in a cloud session")
    ap.add_argument("--pattern", default=DEFAULT_PATTERN,
                    help="repo naming convention (default: %s)" % DEFAULT_PATTERN)
    ap.add_argument("--no-pattern", action="store_true", help="disable convention matching")
    ap.add_argument("--roster", help="CSV or JSON mapping repos/handles to real names")
    ap.add_argument("--days", type=int, help="window length in days")
    ap.add_argument("--since", help="window start (ISO date)")
    ap.add_argument("--until", help="window end (ISO date, default: now)")
    ap.add_argument("--data-dir", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                       os.pardir, "data"),
                    help="where snapshots are kept")
    ap.add_argument("--out", help="write the full JSON report here")
    ap.add_argument("--md", help="write the Markdown briefing here")
    ap.add_argument("--html", help="write the HTML briefing here")
    ap.add_argument("--jobs", type=int, default=6, help="parallel repo fetches (default 6)")
    ap.add_argument("--max-repos", type=int, help="stop after N repos (useful for a dry run)")
    ap.add_argument("--max-commit-details", type=int, default=40,
                    help="per-repo cap on commit-size lookups (default 40)")
    ap.add_argument("--loc-mode", choices=("estimate", "exact", "off"), default="estimate",
                    help="lines of code: estimate from blob sizes (free, default), "
                         "exact (one API call per code file), or off")
    ap.add_argument("--exclude", help="regex of repo names to skip during org enumeration, "
                                     "e.g. shared/template repos that aren't anyone's work")
    ap.add_argument("--include-forks", action="store_true")
    ap.add_argument("--include-archived", action="store_true")
    ap.add_argument("--no-gh", action="store_true", help="force the REST backend")
    ap.add_argument("--no-snapshot", action="store_true", help="do not write a snapshot")
    ap.add_argument("--verbose", action="store_true")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    gh = GitHub(prefer_gh=not args.no_gh, verbose=args.verbose)

    data_dir = os.path.abspath(args.data_dir)
    snap_path = snapshot_dir(data_dir, args.org)
    snapshot = load_snapshot(snap_path)
    since, until, compared_to = resolve_window(args, snapshot)

    if args.repos or args.repos_from:
        repo_names, skipped = [], 0
        if args.repos:
            repo_names += [r.strip() for r in args.repos.split(",") if r.strip()]
        if args.repos_from:
            if not os.path.exists(args.repos_from):
                raise SystemExit("repo list file not found: %s" % args.repos_from)
            if args.repos_from.lower().endswith((".csv", ".tsv", ".json")):
                repo_names += repos_from_roster(args.repos_from, args.org)
            else:
                with open(args.repos_from) as fh:
                    for line in fh:
                        line = line.split("#", 1)[0].strip()
                        if not line:
                            continue
                        repo_names.append(line if "/" in line else "%s/%s" % (args.org, line))
        seen, deduped = set(), []
        for name in repo_names:
            if name not in seen:
                seen.add(name)
                deduped.append(name)
        repo_names = deduped
    else:
        exclude_re = re.compile(args.exclude, re.I) if args.exclude else None
        repo_names, skipped = list_org_repos(gh, args.org, args.include_forks,
                                             args.include_archived, exclude_re)
    if args.max_repos:
        repo_names = repo_names[:args.max_repos]
    if not repo_names:
        raise SystemExit("No repos found for org '%s'." % args.org)

    if args.verbose:
        print("[scan] %d repos, window %s .. %s, backend=%s"
              % (len(repo_names), iso(since), iso(until), gh.backend), file=sys.stderr)

    def fetch(full_name):
        try:
            return collect_repo(gh, full_name, since, until, args.max_commit_details,
                                args.loc_mode)
        except GitHubError as exc:
            return {"full_name": full_name, "error": str(exc)}

    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        raw_repos = list(pool.map(fetch, repo_names))

    roster_by_repo, roster_by_handle = load_roster(args.roster)
    pattern_re = None if args.no_pattern else pattern_to_regex(args.pattern)
    prev_repos = (snapshot or {}).get("repos", {})
    prev_participants = (snapshot or {}).get("participants", {})

    grouped, sources, inferred = {}, {}, []
    for repo in raw_repos:
        short = repo["full_name"].split("/")[-1]
        name, handle, source = map_repo(short, roster_by_repo, pattern_re)
        top_author = ((repo.get("recent_history") or {}).get("authors") or {})
        if not handle and top_author:
            handle = max(top_author, key=top_author.get)
        if handle and handle.lower() in roster_by_handle and source != "roster":
            name = roster_by_handle[handle.lower()]["participant"]
            source = "roster"
        if source == "inferred":
            inferred.append(repo["full_name"])
        result = analyse_repo(repo, since, until, prev_repos.get(repo["full_name"]))
        grouped.setdefault(name, {"handle": handle, "results": []})
        grouped[name]["results"].append(result)
        if handle and not grouped[name]["handle"]:
            grouped[name]["handle"] = handle
        sources[name] = source

    entries = [
        summarise_participant(name, data["handle"], data["results"],
                              prev_participants.get(name))
        for name, data in grouped.items()
    ]
    entries = order_by_urgency(entries)

    report = {
        "meta": {
            "org": args.org,
            "since": iso(since),
            "until": iso(until),
            "window_days": max(1, round((until - since).total_seconds() / 86400)),
            "generated_at": iso(datetime.now(timezone.utc)),
            "compared_to": compared_to,
            "repos_scanned": len(raw_repos),
            "repos_skipped": skipped,
            "api_calls": gh.calls,
            "backend": gh.backend,
            "pattern": None if args.no_pattern else args.pattern,
            "loc_mode": args.loc_mode,
            "roster": os.path.abspath(args.roster) if args.roster else None,
            "snapshot_path": ("not written (--no-snapshot)" if args.no_snapshot
                              else os.path.join(snap_path, "latest.json")),
        },
        "participants": entries,
        "unmapped": inferred,
    }

    if not args.no_snapshot:
        report["meta"]["snapshot_path"] = write_snapshot(snap_path, report)

    if args.out:
        with open(args.out, "w") as fh:
            json.dump(report, fh, indent=2)
    if args.md:
        with open(args.md, "w") as fh:
            fh.write(to_markdown(report) + "\n")
    if args.html:
        with open(args.html, "w") as fh:
            fh.write(to_html(report))
    if not (args.out or args.md or args.html):
        print(to_markdown(report))
    else:
        written = [p for p in (args.out, args.md, args.html) if p]
        print("Wrote: %s" % ", ".join(written), file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except GitHubError as exc:
        print("error: %s" % exc, file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        sys.exit(130)
