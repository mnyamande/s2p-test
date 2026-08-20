#!/usr/bin/env python3
"""Turn raw per-repo signals into a status, concrete evidence, and an angle.

Every threshold lives in THRESHOLDS so it can be tuned in one place; see
reference/signals.md for why each one is set where it is.
"""

import re
from collections import Counter
from datetime import timedelta

from collect import parse_ts

THRESHOLDS = {
    "stalled_days": 10,        # no commits for this long -> needs intervention
    "quiet_days": 5,           # ...this long -> watch
    "decline_ratio": 0.4,      # window commits / previous window commits
    "min_prev_for_trend": 3,   # don't call a trend off 1-2 commits
    "trivial_msg_ratio": 0.5,
    "min_msgs_for_quality": 4,
    "fixup_streak": 3,
    "fixup_streak_severe": 5,
    "big_dump_lines": 500,
    "big_dump_share": 0.8,
    "ci_fail_streak": 2,
    "ci_fail_streak_severe": 3,
    "silence_gap_days": 4,
    "burst_minutes": 20,
    "burst_commits": 5,
    "rewrite_skew_hours": 24,
    "rewrite_min_commits": 3,
    "new_repo_grace_days": 3,
}

TRIVIAL_RE = re.compile(
    r"^(wip|update[sd]?|updating|fix|fixes|fixed|changes?|edit|edits|stuff|misc|"
    r"test|tests|tmp|temp|asdf|foo|bar|commit|save|saving|minor|cleanup|clean up|"
    r"more|again|final|done|x+|\.+)\W*$",
    re.I,
)
FIXUP_RE = re.compile(
    r"^(fix|fixup|fixed|fixing|revert|reverts|reverting|undo|retry|re-?try|oops|"
    r"typo|again|another|please|argh|ugh|hotfix|patch|actually|really|attempt)\b",
    re.I,
)

SEV_INFO, SEV_WATCH, SEV_INTERVENE = 0, 1, 2
STATUS_BY_SEV = {SEV_INFO: "On track", SEV_WATCH: "Watch", SEV_INTERVENE: "Needs intervention"}


def _flag(code, severity, evidence, angle=None, weight=None):
    return {"code": code, "severity": severity, "evidence": evidence,
            "angle": angle, "weight": weight if weight is not None else severity * 10}


def _daily_buckets(commits, since, window_days):
    """Commits per day across the window, oldest day first (drives sparklines)."""
    buckets = [0] * window_days
    for c in commits:
        dt = parse_ts(c.get("committer_date"))
        if not dt:
            continue
        idx = int((dt - since).total_seconds() // 86400)
        if 0 <= idx < window_days:
            buckets[idx] += 1
    return buckets


def _plural(n, word):
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def _fmt_date(dt):
    return dt.strftime("%b %-d") if dt else "unknown"


def analyse_repo(repo, since, until, prev_record=None):
    """Return metrics + flags for one repo."""
    result = {"repo": repo["full_name"], "html_url": repo.get("html_url"),
              "error": repo.get("error"), "flags": [], "metrics": {}}
    if repo.get("error"):
        result["flags"].append(_flag("unreadable", SEV_WATCH,
                                     "Repo could not be read: %s" % repo["error"],
                                     "Confirm the repo exists and you have read access."))
        return result

    window_days = max(1, round((until - since).total_seconds() / 86400))
    commits = repo.get("commits") or []
    chron = sorted(commits, key=lambda c: c.get("committer_date") or "")
    m = result["metrics"]
    m["window_days"] = window_days
    m["commits"] = len(commits)
    m["commits_prev"] = repo.get("prev_commit_count", 0)
    m["active_days"] = len({(c.get("committer_date") or "")[:10] for c in commits if c.get("committer_date")})
    m["per_day"] = round(len(commits) / float(window_days), 2)
    m["daily"] = _daily_buckets(commits, since, window_days)
    m["archived"] = bool(repo.get("archived"))

    history = repo.get("recent_history") or {}
    last_dt = parse_ts(history.get("last_date")) or parse_ts(repo.get("pushed_at"))
    m["last_commit_date"] = history.get("last_date")
    m["last_commit_message"] = history.get("last_message")
    m["last_commit_sha"] = history.get("last_sha")
    m["days_since_last_commit"] = (
        max(0, int((until - last_dt).total_seconds() // 86400)) if last_dt else None
    )
    m["ever_committed"] = bool(history.get("sampled_count"))
    if not m["ever_committed"]:
        m["days_since_last_commit"] = None
    authors = history.get("authors") or {}
    m["top_author"] = max(authors, key=authors.get) if authors else None

    created = parse_ts(repo.get("created_at"))
    m["repo_age_days"] = int((until - created).total_seconds() // 86400) if created else None
    brand_new = (m["repo_age_days"] is not None
                 and m["repo_age_days"] <= THRESHOLDS["new_repo_grace_days"])

    if m["archived"]:
        result["flags"].append(_flag("archived", SEV_INFO, "Repo is archived — skipping analysis."))
        return result

    _momentum_flags(result, m, brand_new, until)
    _style_flags(result, m, commits, chron)
    _struggle_flags(result, m, chron, repo)
    _ci_flags(result, m, repo, since)
    _claude_flags(result, m, repo)
    _structure_flags(result, m, repo, brand_new)
    _change_flags(result, m, prev_record, repo)
    return result


# -- momentum --------------------------------------------------------------

def _momentum_flags(result, m, brand_new, until):
    days = m["days_since_last_commit"]
    n, prev = m["commits"], m["commits_prev"]

    if not m["ever_committed"]:
        result["flags"].append(_flag(
            "never_started", SEV_INTERVENE,
            "Repo exists but has no commits at all%s" % (
                " (created %s days ago)" % m["repo_age_days"] if m["repo_age_days"] else ""),
            "Hasn't started — check they cloned the repo and got Claude Code running at all.",
            weight=30))
        return

    if days is None:
        pass
    elif days >= THRESHOLDS["stalled_days"]:
        prior = ("prior %d-day window had %s" % (m["window_days"], _plural(prev, "commit"))
                 if prev else "no activity in the prior window either")
        result["flags"].append(_flag(
            "stalled", SEV_INTERVENE,
            "No commits in %d days (last: %s) — %s" % (days, _fmt_date(parse_ts(m["last_commit_date"])), prior),
            "Silent for over a week — reach out directly; likely blocked or dropped off rather than just busy.",
            weight=25))
    elif days >= THRESHOLDS["quiet_days"] and not brand_new:
        result["flags"].append(_flag(
            "quiet", SEV_WATCH,
            "No commits in %d days (last: %s)" % (days, _fmt_date(parse_ts(m["last_commit_date"]))),
            "Went quiet mid-week — a light check-in is probably enough.",
            weight=12))

    if prev >= THRESHOLDS["min_prev_for_trend"]:
        ratio = n / float(prev)
        m["trend_ratio"] = round(ratio, 2)
        if ratio < THRESHOLDS["decline_ratio"]:
            m["trend"] = "declining"
            result["flags"].append(_flag(
                "declining", SEV_WATCH,
                "Commits dropped from %d to %d vs the previous %d days" % (prev, n, m["window_days"]),
                "Momentum is falling off — worth asking what changed since last week.",
                weight=14))
        elif ratio > 1.3:
            m["trend"] = "accelerating"
            result["flags"].append(_flag(
                "accelerating", SEV_INFO,
                "%s over %d days, up from %d in the previous window"
                % (_plural(n, "commit"), m["window_days"], prev)))
        else:
            m["trend"] = "steady"
    else:
        m["trend"] = "insufficient history"

    if n and m["active_days"] >= 3 and m["days_since_last_commit"] is not None \
            and m["days_since_last_commit"] <= 2 and not result["flags"]:
        result["flags"].append(_flag(
            "healthy_cadence", SEV_INFO,
            "%s across %s in the last %d days"
            % (_plural(n, "commit"), _plural(m["active_days"], "active day"), m["window_days"])))


# -- working style ---------------------------------------------------------

def _style_flags(result, m, commits, chron):
    sized = [c for c in commits if c.get("additions") is not None]
    m["commits_measured"] = len(sized)
    if sized:
        totals = sorted((c["additions"] + c["deletions"]) for c in sized)
        m["median_commit_lines"] = totals[len(totals) // 2]
        m["max_commit_lines"] = totals[-1]
        grand = sum(totals) or 1
        biggest = sized[max(range(len(sized)), key=lambda i: sized[i]["additions"] + sized[i]["deletions"])]
        big_lines = biggest["additions"] + biggest["deletions"]
        share = big_lines / float(grand)
        m["largest_commit_share"] = round(share, 2)
        if (big_lines >= THRESHOLDS["big_dump_lines"]
                and share >= THRESHOLDS["big_dump_share"] and len(sized) <= 5):
            result["flags"].append(_flag(
                "big_dump", SEV_WATCH,
                "One %s-line commit is %d%% of all changes in the window (%s total)"
                % ("{:,}".format(big_lines), round(share * 100), _plural(len(commits), "commit")),
                "Working in big batches rather than iterating — suggest smaller commits so Claude Code has tighter feedback loops.",
                weight=10))
        elif len(sized) >= 5 and m["median_commit_lines"] <= 120:
            result["flags"].append(_flag(
                "iterative", SEV_INFO,
                "Small iterative commits (median %d lines changed)" % m["median_commit_lines"]))

    msgs = [c["message"].splitlines()[0] for c in commits if c.get("message")]
    m["trivial_msg_count"] = sum(1 for msg in msgs if TRIVIAL_RE.match(msg.strip()) or len(msg.strip()) < 8)
    if len(msgs) >= THRESHOLDS["min_msgs_for_quality"]:
        ratio = m["trivial_msg_count"] / float(len(msgs))
        m["trivial_msg_ratio"] = round(ratio, 2)
        if ratio >= THRESHOLDS["trivial_msg_ratio"]:
            sample = [msg for msg in msgs if TRIVIAL_RE.match(msg.strip()) or len(msg.strip()) < 8][:3]
            result["flags"].append(_flag(
                "low_signal_messages", SEV_INFO,
                "%d of %d commit messages are placeholder-level (%s)"
                % (m["trivial_msg_count"], len(msgs),
                   ", ".join('"%s"' % s for s in sample)),
                weight=4))


# -- struggle markers ------------------------------------------------------

def _struggle_flags(result, m, chron, repo):
    msgs = [(c.get("message") or "").splitlines()[0].strip() for c in chron]
    best, best_start, run, run_start = 0, 0, 0, 0
    for i, msg in enumerate(msgs):
        if FIXUP_RE.match(msg):
            run = run + 1 if run else 1
            run_start = run_start if run > 1 else i
            if run > best:
                best, best_start = run, run_start
        else:
            run = 0
    m["fixup_streak"] = best
    if best >= THRESHOLDS["fixup_streak"]:
        sample = msgs[best_start:best_start + min(best, 4)]
        sev = SEV_INTERVENE if best >= THRESHOLDS["fixup_streak_severe"] else SEV_WATCH
        result["flags"].append(_flag(
            "fixup_streak", sev,
            "%d consecutive fix/revert commits: %s"
            % (best, " → ".join('"%s"' % s[:40] for s in sample)),
            "Looks like a loop they can't break out of — ask what the last few 'fix' commits were chasing.",
            weight=20 if sev == SEV_INTERVENE else 13))

    # Long silence followed by a burst: local struggle, then a dump.
    gap_days, burst = 0, 0
    for i in range(1, len(chron)):
        prev_dt = parse_ts(chron[i - 1].get("committer_date"))
        cur_dt = parse_ts(chron[i].get("committer_date"))
        if not prev_dt or not cur_dt:
            continue
        gap = (cur_dt - prev_dt).total_seconds() / 86400.0
        if gap >= THRESHOLDS["silence_gap_days"]:
            window_end = cur_dt + timedelta(minutes=THRESHOLDS["burst_minutes"])
            count = sum(1 for c in chron[i:]
                        if parse_ts(c.get("committer_date"))
                        and parse_ts(c.get("committer_date")) <= window_end)
            if count >= THRESHOLDS["burst_commits"] and count > burst:
                gap_days, burst = int(gap), count
    m["dump_after_silence"] = {"gap_days": gap_days, "burst_commits": burst} if burst else None
    if burst:
        result["flags"].append(_flag(
            "dump_after_silence", SEV_WATCH,
            "%d-day silence then %d commits pushed within %d minutes"
            % (gap_days, burst, THRESHOLDS["burst_minutes"]),
            "Pattern of working locally then dumping — they may be stuck for days before asking; encourage pushing early.",
            weight=11))

    rewritten = 0
    for c in chron:
        a, cd = parse_ts(c.get("author_date")), parse_ts(c.get("committer_date"))
        if a and cd and (cd - a).total_seconds() > THRESHOLDS["rewrite_skew_hours"] * 3600:
            rewritten += 1
    m["rewritten_commits"] = rewritten
    if rewritten >= THRESHOLDS["rewrite_min_commits"]:
        result["flags"].append(_flag(
            "history_rewrite", SEV_INFO,
            "%d commits show rebase/squash rewriting (authored well before they landed)" % rewritten,
            weight=5))


# -- CI --------------------------------------------------------------------

def _ci_flags(result, m, repo, since):
    actions = repo.get("actions") or {}
    m["ci_configured"] = bool(actions.get("configured"))
    if not actions.get("configured"):
        return
    runs = [r for r in actions.get("runs", []) if r.get("conclusion")]
    runs.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    latest_by_sha, order = {}, []
    for run in runs:
        sha = run.get("head_sha")
        if sha not in latest_by_sha:
            latest_by_sha[sha] = run
            order.append(sha)
    streak, first_fail, name = 0, None, None
    for sha in order:
        run = latest_by_sha[sha]
        if run.get("conclusion") == "failure":
            streak += 1
            first_fail = run.get("created_at")
            name = name or run.get("name")
        else:
            break
    m["ci_fail_streak"] = streak
    m["ci_latest_conclusion"] = latest_by_sha[order[0]].get("conclusion") if order else None
    m["ci_first_failure_at"] = first_fail
    m["ci_workflow"] = name
    if streak >= THRESHOLDS["ci_fail_streak"]:
        sev = SEV_INTERVENE if streak >= THRESHOLDS["ci_fail_streak_severe"] else SEV_WATCH
        result["flags"].append(_flag(
            "ci_failing", sev,
            'CI red on %s consecutive commits since %s (workflow "%s")'
            % (streak, _fmt_date(parse_ts(first_fail)), name or "unknown"),
            "Stuck on the same CI failure for days — look at the test/build setup with them specifically.",
            weight=22 if sev == SEV_INTERVENE else 15))
    elif m["ci_latest_conclusion"] == "success" and m["commits"]:
        result["flags"].append(_flag("ci_green", SEV_INFO, "CI green on the latest commit"))


# -- Claude Code usage (bonus signal, never counted against anyone) --------

def _claude_flags(result, m, repo):
    cmd = repo.get("claude_md") or {}
    m["claude_md"] = {"present": cmd.get("present"), "revisions": cmd.get("commit_count"),
                      "last_updated": cmd.get("last_updated"),
                      "committed_claude_dir": cmd.get("committed_claude_dir")}
    if not cmd.get("present"):
        return
    revs = cmd.get("commit_count") or 0
    if revs >= 3:
        result["flags"].append(_flag(
            "claude_md_evolving", SEV_INFO,
            "CLAUDE.md revised %d times (last %s) — project context is being maintained"
            % (revs, _fmt_date(parse_ts(cmd.get("last_updated"))))))
    else:
        result["flags"].append(_flag(
            "claude_md_static", SEV_INFO,
            "CLAUDE.md written once and not revised since %s"
            % _fmt_date(parse_ts(cmd.get("first_seen") or cmd.get("last_updated")))))


# -- structure -------------------------------------------------------------

def _structure_flags(result, m, repo, brand_new):
    tree = repo.get("tree") or {}
    m["file_count"] = tree.get("file_count")
    m["has_readme"] = tree.get("has_readme")
    m["has_tests"] = tree.get("has_tests")
    m["size_kb"] = repo.get("size_kb")
    if not tree.get("available") or brand_new:
        return
    missing = []
    if not tree.get("has_readme"):
        missing.append("no README")
    if not tree.get("has_tests"):
        missing.append("no tests")
    if missing and m["commits"]:
        result["flags"].append(_flag(
            "thin_structure", SEV_INFO,
            "%s across %s tracked files" % (" and ".join(missing).capitalize(), tree.get("file_count")),
            weight=3))


# -- run-over-run change ---------------------------------------------------

def _change_flags(result, m, prev_record, repo):
    if not prev_record:
        m["is_new_since_last_run"] = True
        return
    m["is_new_since_last_run"] = False
    prev_status = prev_record.get("status")
    prev_last_sha = (prev_record.get("metrics") or {}).get("last_commit_sha")
    last_sha = m.get("last_commit_sha")
    prev_run_at = prev_record.get("run_at")
    if last_sha and prev_last_sha and last_sha == prev_last_sha:
        result["flags"].append(_flag(
            "no_movement_since_last_run", SEV_WATCH,
            "No new commits since the last scan on %s — same HEAD" % _fmt_date(parse_ts(prev_run_at)),
            "Already flagged at the last run and nothing has moved — escalate from a nudge to a conversation.",
            weight=16))
    prev_ci = (prev_record.get("metrics") or {}).get("ci_fail_streak") or 0
    if prev_ci and (m.get("ci_fail_streak") or 0) >= prev_ci:
        result["flags"].append(_flag(
            "ci_still_failing", SEV_WATCH,
            "CI was already failing at the last scan and still is",
            "The same build has been red across two scans — this one won't unstick itself.",
            weight=17))
    m["previous_status"] = prev_status


# -- roll-up ---------------------------------------------------------------

def status_for(flags):
    sev = max([f["severity"] for f in flags], default=SEV_INFO)
    return STATUS_BY_SEV[sev]


def summarise_participant(name, handle, repo_results, prev_participant=None):
    """Merge one participant's repos into a single ranked briefing entry."""
    flags = []
    multi = len(repo_results) > 1
    for res in repo_results:
        for flag in res["flags"]:
            item = dict(flag)
            if multi:
                item["evidence"] = "%s: %s" % (res["repo"].split("/")[-1], item["evidence"])
            flags.append(item)

    real = [f for f in flags if f["severity"] > SEV_INFO]
    ranked = sorted(flags, key=lambda f: (-f["severity"], -f["weight"]))
    status = status_for(flags)
    score = sum(f["weight"] for f in real)
    if status == "On track":
        evidence = [f["evidence"] for f in ranked if f["severity"] == SEV_INFO][:2]
    else:
        # Lead with the signals that set the status; add context only if thin.
        evidence = [f["evidence"] for f in ranked if f["severity"] > SEV_INFO][:4]
        if len(evidence) < 2:
            context = [f["evidence"] for f in ranked if f["severity"] == SEV_INFO]
            evidence += context[:2 - len(evidence)]
    angle = next((f["angle"] for f in ranked if f.get("angle")), None)

    metrics = repo_results[0]["metrics"] if repo_results else {}
    day_values = [r["metrics"].get("days_since_last_commit") for r in repo_results
                  if r["metrics"].get("days_since_last_commit") is not None]
    days = min(day_values) if day_values else None
    daily = []
    for res in repo_results:
        series = res["metrics"].get("daily") or []
        if not daily:
            daily = list(series)
        else:
            for i, value in enumerate(series[:len(daily)]):
                daily[i] += value

    entry = {
        "participant": name,
        "daily": daily,
        "handle": handle,
        "repos": [r["repo"] for r in repo_results],
        "urls": [r.get("html_url") for r in repo_results],
        "status": status,
        "score": score,
        "days_since_last_commit": days,
        "commits": sum(r["metrics"].get("commits", 0) for r in repo_results),
        "commits_prev": sum(r["metrics"].get("commits_prev", 0) for r in repo_results),
        "evidence": evidence,
        "angle": angle if status != "On track" else None,
        "flag_codes": [f["code"] for f in ranked],
        "metrics": metrics,
        "repo_details": repo_results,
    }
    if prev_participant and prev_participant.get("status") != status:
        entry["status_change"] = "%s → %s since last run" % (prev_participant["status"], status)
    return entry


def order_by_urgency(entries):
    rank = {"Needs intervention": 0, "Watch": 1, "On track": 2}
    return sorted(entries, key=lambda e: (rank.get(e["status"], 3), -e["score"],
                                          -(e["days_since_last_commit"] or 0),
                                          e["participant"].lower()))
