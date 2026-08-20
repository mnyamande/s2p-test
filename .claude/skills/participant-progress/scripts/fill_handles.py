#!/usr/bin/env python3
"""Fill the `github handle` column of a roster CSV from each repo's authors.

Read-only against GitHub, read-write against the CSV. Run it where the org is
reachable — your own terminal with `gh auth login`, not a cloud session:

    python3 fill_handles.py --org agentic-ai-coop --csv ../../../../cohort/roster.csv

Only blank handles are filled, so re-running is safe and never overwrites a
handle you corrected by hand. Rows it cannot resolve are listed at the end and
left blank rather than guessed at.
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
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ghclient import GitHub, GitHubError  # noqa: E402

# Accounts that show up as authors but are never the participant.
BOT_LOGINS = {"claude", "claude-bot", "claude[bot]", "github-actions",
              "github-actions[bot]", "dependabot", "dependabot[bot]",
              "web-flow", "copilot", "github-copilot[bot]"}


def is_bot(login):
    low = (login or "").lower()
    return not low or low in BOT_LOGINS or low.endswith("[bot]")


def authors_for(gh, org, repo):
    """[(login, commits)] for a repo, most prolific first, bots removed."""
    base = "/repos/%s/%s" % (org, repo)
    people = []
    try:
        contributors = gh.paginate(base + "/contributors", {"per_page": 30}, max_items=30)
    except GitHubError as exc:
        if exc.status in (403, 401):
            raise  # a real access problem — say so rather than reporting "no authors"
        contributors = []  # 404/409: empty or brand-new repo, try commits below
    for entry in contributors or []:
        login = entry.get("login")
        if entry.get("type") == "Bot" or is_bot(login):
            continue
        people.append((login, entry.get("contributions") or 0))
    if people:
        return sorted(people, key=lambda x: -x[1])

    # Empty or brand-new repo: contributors can lag, so fall back to commits.
    tally = {}
    for item in gh.paginate(base + "/commits", {"per_page": 100},
                            max_items=100, accept_missing=True) or []:
        login = (item.get("author") or {}).get("login")
        if not is_bot(login):
            tally[login] = tally.get(login, 0) + 1
    return sorted(tally.items(), key=lambda x: -x[1])


def read_csv(path):
    """Return (comment lines, fieldnames, rows) so comments survive a rewrite."""
    comments, body = [], []
    with open(path, newline="") as fh:
        for line in fh:
            if not body and line.lstrip().startswith("#"):
                comments.append(line.rstrip("\n"))
            else:
                body.append(line)
    reader = csv.DictReader(body)
    return comments, reader.fieldnames, list(reader)


def write_csv(path, comments, fieldnames, rows):
    with open(path, "w", newline="") as fh:
        for line in comments:
            fh.write(line + "\n")
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def column(fieldnames, *candidates):
    for name in fieldnames:
        if name.strip().lower() in candidates:
            return name
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", required=True, help="roster CSV to update in place")
    ap.add_argument("--org", required=True, help="GitHub org the repos live in")
    ap.add_argument("--facilitator", action="append", default=[],
                    help="handle that set up the repos; deprioritised when a repo "
                         "has another human author. Repeatable")
    ap.add_argument("--overwrite", action="store_true",
                    help="also refill handles that are already set")
    ap.add_argument("--dry-run", action="store_true", help="show changes, write nothing")
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--no-gh", action="store_true")
    args = ap.parse_args(argv)

    comments, fieldnames, rows = read_csv(args.csv)
    repo_col = column(fieldnames, "repo", "repository", "repo name")
    handle_col = column(fieldnames, "github handle", "handle", "github", "username", "login")
    if not repo_col or not handle_col:
        raise SystemExit("CSV needs a repo column and a github handle column; got %s"
                         % fieldnames)

    gh = GitHub(prefer_gh=not args.no_gh)
    facilitators = {f.lower() for f in args.facilitator}
    todo = [r for r in rows if r.get(repo_col)
            and (args.overwrite or not (r.get(handle_col) or "").strip())]
    print("Resolving %d of %d rows via %s ..." % (len(todo), len(rows), gh.backend),
          file=sys.stderr)

    from concurrent.futures import ThreadPoolExecutor

    def resolve(row):
        try:
            return row, authors_for(gh, args.org, row[repo_col]), None
        except GitHubError as exc:
            return row, [], str(exc)

    filled, unresolved, ambiguous = 0, [], []
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        for row, people, error in pool.map(resolve, todo):
            repo = row[repo_col]
            if error:
                unresolved.append((repo, error[:80]))
                continue
            humans = [p for p in people if p[0].lower() not in facilitators]
            pick = humans or people
            if not pick:
                unresolved.append((repo, "no non-bot authors found"))
                continue
            row[handle_col] = pick[0][0]
            filled += 1
            if len(pick) > 1 and pick[1][1] >= pick[0][1]:
                ambiguous.append((repo, [p[0] for p in pick[:3]]))

    if args.dry_run:
        for row in todo:
            if row.get(handle_col):
                print("%-34s -> %s" % (row[repo_col], row[handle_col]))
    else:
        write_csv(args.csv, comments, fieldnames, rows)

    print("\nFilled %d handle%s%s." % (filled, "" if filled == 1 else "s",
                                       " (dry run, nothing written)" if args.dry_run else ""),
          file=sys.stderr)
    if ambiguous:
        print("\nTied author counts — confirm these by hand:", file=sys.stderr)
        for repo, logins in ambiguous:
            print("  %-34s %s" % (repo, ", ".join(logins)), file=sys.stderr)
    if unresolved:
        print("\nLeft blank (resolve by hand):", file=sys.stderr)
        for repo, why in unresolved:
            print("  %-34s %s" % (repo, why), file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except GitHubError as exc:
        print("error: %s" % exc, file=sys.stderr)
        sys.exit(2)
