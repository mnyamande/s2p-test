#!/usr/bin/env python3
"""End-to-end test on a synthetic cohort. No network, no credentials.

Builds a fake org whose repos each exhibit one behaviour we claim to detect,
runs the real scan/analyse/render pipeline over it, and asserts the briefing
says what it should. Run it after changing any threshold in analyze.py:

    python3 selftest.py
"""

import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import scan  # noqa: E402
from collect import iso  # noqa: E402

NOW = datetime.now(timezone.utc)
ORG = "test-cohort"


def _blob_size(path):
    """Deterministic pretend file sizes so LOC estimates are stable across runs."""
    if path.lower().startswith("readme"):
        return 2400
    if path == "CLAUDE.md":
        return 1800
    return 1280


def ago(days=0, hours=0, minutes=0):
    return NOW - timedelta(days=days, hours=hours, minutes=minutes)


def commit(sha, message, when, additions=30, deletions=8, authored=None, files=3):
    return {"sha": sha, "message": message,
            "author_date": iso(authored or when), "committer_date": iso(when),
            "additions": additions, "deletions": deletions, "files": files}


def spread(prefix, count, start_days_ago, step_days=1.0, message="implement %d", size=40):
    out = []
    for i in range(count):
        when = ago(days=start_days_ago - i * step_days)
        out.append(commit("%s%02d" % (prefix, i), message % i if "%d" in message else message,
                          when, additions=size, deletions=size // 4))
    return out


def repo(name, commits, runs=None, paths=None, created_days_ago=60, claude_md=0):
    return {
        "name": name,
        "meta": {
            "name": name, "full_name": "%s/%s" % (ORG, name),
            "html_url": "https://github.com/%s/%s" % (ORG, name),
            "default_branch": "main", "private": True, "archived": False, "fork": False,
            "created_at": iso(ago(days=created_days_ago)), "pushed_at": iso(NOW),
            "size": 480, "description": "",
        },
        "commits": sorted(commits, key=lambda c: c["committer_date"], reverse=True),
        "runs": runs or [],
        "paths": paths if paths is not None else ["README.md", "src/app.py", "tests/test_app.py"],
        "claude_md_revisions": claude_md,
    }


def ci_runs(commits, conclusions, name="tests"):
    """Attach one workflow run per commit. conclusions[0] is the NEWEST commit."""
    out = []
    newest_first = sorted(commits, key=lambda c: c["committer_date"], reverse=True)
    for c, conclusion in zip(newest_first, conclusions):
        out.append({"name": name, "head_sha": c["sha"], "status": "completed",
                    "conclusion": conclusion, "created_at": c["committer_date"],
                    "html_url": "https://github.com/run", "event": "push"})
    return out


def build_cohort():
    repos = []

    # 1. Healthy: near-daily small commits, CI green, CLAUDE.md maintained.
    healthy = spread("aa", 11, 12, 1.1, "add %d: parser stage", size=45)
    repos.append(repo("s2p-ada-lovelace", healthy,
                      runs=ci_runs(healthy, ["success"] * 11),
                      paths=["README.md", "CLAUDE.md", "src/a.py", "tests/test_a.py"],
                      claude_md=5))

    # 2. Stalled: was productive, nothing for 12 days.
    stalled = spread("bb", 9, 26, 1.0, "step %d")
    repos.append(repo("s2p-grace-hopper", stalled, runs=ci_runs(stalled, ["success"] * 9)))

    # 3. Persistent CI failure across four commits.
    ci_stuck = spread("cc", 6, 5, 0.7, "try workflow tweak %d")
    repos.append(repo("s2p-alan-turing", ci_stuck,
                      runs=ci_runs(ci_stuck, ["failure"] * 4 + ["success"] * 2)))

    # 4. Fix-up loop: six consecutive fix/revert commits.
    loop = [commit("dd%d" % i, msg, ago(days=3, hours=12 - i * 2)) for i, msg in enumerate([
        "add auth middleware", "fix auth", "fix auth again", "revert auth change",
        "fix imports", "fix tests", "oops missing file"])]
    repos.append(repo("s2p-katherine-johnson", loop, runs=ci_runs(loop, ["failure", "success"])))

    # 5. One big dump, nothing else.
    dump = [commit("ee0", "initial project", ago(days=6), additions=1800, deletions=40, files=42)]
    repos.append(repo("s2p-margaret-hamilton", dump, paths=["src/main.py"]))

    # 6. Long silence, then a burst of local work pushed at once.
    burst = [commit("ff0", "scaffold", ago(days=13))]
    burst += [commit("ff%d" % (i + 1), "work chunk %d" % i, ago(days=2, minutes=14 - i * 2))
              for i in range(7)]
    repos.append(repo("s2p-radia-perlman", burst))

    # 7. Empty repo: created, never used.
    repos.append(repo("s2p-hedy-lamarr", [], paths=[], created_days_ago=21))

    # 8. Declining: heavy previous window, thin this one.
    declining = spread("gg", 2, 4, 1.0, "small tweak %d")
    declining += spread("hh", 14, 25, 0.8, "build feature %d")
    repos.append(repo("s2p-jean-bartik", declining))

    # 9. Roster exception: repo name does not follow the convention.
    odd = spread("ii", 8, 9, 1.0, "refactor %d")
    repos.append(repo("cohort-project-sf", odd, runs=ci_runs(odd, ["success"] * 8)))

    return {r["meta"]["full_name"]: r for r in repos}


class FakeGitHub:
    """Serves exactly the endpoints collect.py asks for, from the fixture."""

    backend = "fixture"

    def __init__(self, db):
        self.db = db
        self.calls = 0

    # -- routing
    def get(self, path, params=None, accept_missing=False):
        self.calls += 1
        params = params or {}
        m = re.match(r"^/repos/([^/]+/[^/]+)$", path)
        if m:
            entry = self.db.get(m.group(1))
            return entry["meta"] if entry else None
        m = re.match(r"^/repos/([^/]+/[^/]+)/commits/([0-9a-z]+)$", path)
        if m:
            entry = self.db.get(m.group(1))
            for c in entry["commits"]:
                if c["sha"] == m.group(2):
                    return {"sha": c["sha"],
                            "stats": {"additions": c["additions"], "deletions": c["deletions"]},
                            "files": [{"filename": "f%d" % i} for i in range(c["files"])]}
            return None
        m = re.match(r"^/repos/([^/]+/[^/]+)/git/trees/", path)
        if m:
            entry = self.db.get(m.group(1))
            return {"truncated": False,
                    "tree": [{"path": p, "type": "blob", "size": _blob_size(p),
                              "sha": "b" + str(abs(hash(p)) % 10 ** 8)}
                             for p in entry["paths"]]}
        m = re.match(r"^/repos/([^/]+/[^/]+)/git/blobs/", path)
        if m:
            import base64
            body = ("line\n" * 40).encode()
            return {"encoding": "base64", "content": base64.b64encode(body).decode()}
        if "/commits" in path or "/actions/runs" in path:
            return self.paginate(path, params, accept_missing=accept_missing)
        return None

    def paginate(self, path, params=None, max_items=None, accept_missing=False):
        self.calls += 1
        params = params or {}
        m = re.match(r"^/orgs/([^/]+)/repos$", path)
        if m:
            return [r["meta"] for r in self.db.values()]
        m = re.match(r"^/repos/([^/]+/[^/]+)/commits$", path)
        if m:
            entry = self.db.get(m.group(1))
            if entry is None:
                return None
            if params.get("path") == "CLAUDE.md":
                revs = entry["claude_md_revisions"]
                return [self._api_commit(c) for c in entry["commits"][:revs]]
            items = entry["commits"]
            since, until = params.get("since"), params.get("until")
            if since:
                items = [c for c in items if c["committer_date"] >= since]
            if until:
                items = [c for c in items if c["committer_date"] <= until]
            return [self._api_commit(c) for c in items][:max_items or len(items)]
        m = re.match(r"^/repos/([^/]+/[^/]+)/actions/runs$", path)
        if m:
            entry = self.db.get(m.group(1))
            return entry["runs"] if entry else None
        return []

    @staticmethod
    def _api_commit(c):
        return {"sha": c["sha"], "parents": [{"sha": "x"}],
                "author": {"login": "octo-" + c["sha"][:2]},
                "commit": {"message": c["message"],
                           "author": {"name": "Octo", "date": c["author_date"]},
                           "committer": {"name": "Octo", "date": c["committer_date"]}}}


EXPECTED = {
    "Ada Lovelace": "On track",
    "Grace Hopper": "Needs intervention",
    "Alan Turing": "Needs intervention",
    "Katherine Johnson": "Needs intervention",
    "Hedy Lamarr": "Needs intervention",
    "Margaret Hamilton": "Watch",
    "Radia Perlman": "Watch",
    "Jean Bartik": "Watch",
    "Sofia Ferrari": "On track",
}


def run():
    db = build_cohort()
    tmp = tempfile.mkdtemp(prefix="pp-selftest-")
    failures = []
    try:
        roster = os.path.join(tmp, "roster.csv")
        with open(roster, "w") as fh:
            fh.write("repo,participant,github handle\n")
            fh.write("cohort-project-sf,Sofia Ferrari,sferrari\n")

        scan.GitHub = lambda *a, **k: FakeGitHub(db)
        out_json = os.path.join(tmp, "report.json")
        out_md = os.path.join(tmp, "report.md")
        out_html = os.path.join(tmp, "report.html")
        argv = ["--org", ORG, "--roster", roster, "--days", "14", "--jobs", "4",
                "--data-dir", os.path.join(tmp, "data"),
                "--out", out_json, "--md", out_md, "--html", out_html]
        scan.main(argv)

        report = json.load(open(out_json))
        got = {e["participant"]: e["status"] for e in report["participants"]}

        for name, want in EXPECTED.items():
            if got.get(name) != want:
                failures.append("status: %s expected %r, got %r" % (name, want, got.get(name)))

        order = [e["participant"] for e in report["participants"]]
        rank = {"Needs intervention": 0, "Watch": 1, "On track": 2}
        ranks = [rank[got[n]] for n in order]
        if ranks != sorted(ranks):
            failures.append("ordering: not urgency-first: %s" % order)

        by_name = {e["participant"]: e for e in report["participants"]}
        checks = [
            ("Grace Hopper", r"No commits in \d+ days"),
            ("Alan Turing", r"CI red on \d+ consecutive commits"),
            ("Katherine Johnson", r"consecutive fix/revert commits"),
            ("Margaret Hamilton", r"line commit is \d+% of all changes"),
            ("Radia Perlman", r"\d+-day silence then \d+ commits"),
            ("Hedy Lamarr", r"no commits at all"),
            ("Jean Bartik", r"Commits dropped from \d+ to \d+"),
        ]
        for name, pattern in checks:
            entry = by_name.get(name)
            blob = " | ".join(entry["evidence"]) if entry else ""
            if not re.search(pattern, blob):
                failures.append("evidence: %s missing /%s/ (got: %s)" % (name, pattern, blob))

        for name, entry in by_name.items():
            if entry["status"] == "On track":
                if entry.get("angle"):
                    failures.append("angle: %s is On track but carries an angle" % name)
                if len(entry["evidence"]) > 2:
                    failures.append("brevity: On-track %s has %d bullets" % (name, len(entry["evidence"])))
            else:
                if not entry.get("angle"):
                    failures.append("angle: %s (%s) has no suggested angle" % (name, entry["status"]))
                if not 1 <= len(entry["evidence"]) <= 4:
                    failures.append("brevity: %s has %d evidence bullets" % (name, len(entry["evidence"])))

        # -- new-in-v1.1 surfaces: LOC, README pill, depth pill
        ada = by_name.get("Ada Lovelace") or {}
        if (ada.get("depth") or {}).get("level") != "DEEP":
            failures.append("depth: Ada Lovelace expected DEEP, got %r"
                            % (ada.get("depth") or {}).get("level"))
        margaret = by_name.get("Margaret Hamilton") or {}
        if margaret.get("has_readme") is not False:
            failures.append("readme: Margaret Hamilton has no README file but flag is %r"
                            % margaret.get("has_readme"))
        for name, entry in by_name.items():
            if entry.get("loc") is None:
                failures.append("loc: %s has no line count" % name)
            if not (entry.get("depth") or {}).get("level"):
                failures.append("depth: %s has no depth level" % name)

        md = open(out_md).read()
        for needle in ["# Cohort progress", "## Needs intervention", "**Angle:**",
                       "monitored for progress support", "lines of code",
                       "**HAS README**", "**NO README**", "**DEEP**"]:
            if needle not in md:
                failures.append("markdown: missing %r" % needle)
        if "Sofia Ferrari" not in md:
            failures.append("markdown: roster override did not reach the report")
        if "cohort-project-sf" in report["unmapped"]:
            failures.append("mapping: roster-mapped repo was still marked unmapped")

        html_out = open(out_html).read()
        for needle in ["<!doctype html>", "prefers-color-scheme", "class='card intervene'",
                       "Cohort progress", "linear-gradient", "lines of code",
                       ">HAS README<", ">NO README<", "class='chip deep'"]:
            if needle not in html_out:
                failures.append("html: missing %r" % needle)
        if html_out.count("<div") != html_out.count("</div>"):
            failures.append("html: unbalanced <div> tags")

        # -- exact LOC mode counts real blob lines rather than estimating
        out_json3 = os.path.join(tmp, "report3.json")
        scan.main(["--org", ORG, "--days", "14", "--loc-mode", "exact", "--no-snapshot",
                   "--data-dir", os.path.join(tmp, "data-x"), "--out", out_json3])
        exact = json.load(open(out_json3))
        ada3 = next((e for e in exact["participants"] if e["participant"] == "Ada Lovelace"), {})
        if not ada3.get("loc_exact"):
            failures.append("loc: --loc-mode exact did not mark counts as exact")
        # 2 code files (src/a.py, tests/test_a.py) x 40 lines; README.md and
        # CLAUDE.md are docs, not code, so they are excluded by design.
        if ada3.get("loc") != 80:
            failures.append("loc: exact count expected 80, got %r" % ada3.get("loc"))

        # -- second run: snapshot diffing
        snap = os.path.join(tmp, "data", ORG, "latest.json")
        if not os.path.exists(snap):
            failures.append("snapshot: latest.json not written")
        else:
            out_md2 = os.path.join(tmp, "report2.md")
            out_json2 = os.path.join(tmp, "report2.json")
            scan.main(["--org", ORG, "--roster", roster, "--days", "14",
                       "--data-dir", os.path.join(tmp, "data"),
                       "--out", out_json2, "--md", out_md2])
            report2 = json.load(open(out_json2))
            blob = json.dumps(report2)
            if "No new commits since the last scan" not in blob:
                failures.append("diff: second run did not detect an unmoved HEAD")
            if "CI was already failing at the last scan" not in blob:
                failures.append("diff: second run did not detect a persisting CI failure")

        print(open(out_md).read())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 66)
    if failures:
        print("SELFTEST FAILED (%d)" % len(failures))
        for f in failures:
            print("  - %s" % f)
        return 1
    print("SELFTEST PASSED — %d participants, statuses/evidence/ordering/diffing all as expected"
          % len(EXPECTED))
    return 0


if __name__ == "__main__":
    sys.exit(run())
