# Signals, thresholds, and what they mean

Every threshold below is a key in `THRESHOLDS` at the top of `scripts/analyze.py`.
Change them there, then run `python3 scripts/selftest.py`.

Each signal produces a **flag** with one of three severities. A participant's
status is the highest severity across their repos:

| Severity | Status | Meaning |
| --- | --- | --- |
| 2 | Needs intervention | Reach out; they are unlikely to unstick themselves |
| 1 | Watch | Something is off; a light check-in or a look next session |
| 0 | On track | Context only — never changes a status on its own |

Ordering within a status is by summed weight, then by days since last commit.

## Momentum

| Flag | Trigger | Severity |
| --- | --- | --- |
| `never_started` | Repo has no commits at all | 2 |
| `stalled` | No commits for ≥ `stalled_days` (10) | 2 |
| `quiet` | No commits for ≥ `quiet_days` (5) | 1 |
| `declining` | Window commits < 40% of the previous window, and the previous window had ≥ 3 | 1 |
| `accelerating` | Window commits > 130% of the previous window | 0 |
| `healthy_cadence` | ≥ 3 active days and a commit in the last 2 days | 0 |

Ten days is deliberately past "busy week". Five days catches someone who
dropped off mid-week while there is still time to intervene. Trends are not
computed off fewer than 3 prior commits — the ratio is noise below that.

## Working style

| Flag | Trigger | Severity |
| --- | --- | --- |
| `big_dump` | One commit ≥ 500 lines *and* ≥ 80% of the window's changes, with ≤ 5 commits total | 1 |
| `iterative` | ≥ 5 commits with a median under 120 lines changed | 0 |
| `low_signal_messages` | ≥ 50% of messages are placeholders ("wip", "update", "fix") | 0 |

Small iterative commits are the healthier signal for hands-on learning: they
mean the participant is running tight feedback loops rather than accumulating
unreviewed work. Message quality is deliberately severity 0 — it is a coaching
observation, not evidence of being stuck.

Commit size needs a per-commit API call, so only the `--max-commit-details`
most recent commits per repo (default 40) are measured.

## Struggle markers

| Flag | Trigger | Severity |
| --- | --- | --- |
| `fixup_streak` | ≥ 3 consecutive fix/revert-style messages | 1 |
| `fixup_streak` | ≥ 5 consecutive | 2 |
| `ci_failing` | ≥ 2 consecutive commits with a red run | 1 |
| `ci_failing` | ≥ 3 consecutive | 2 |
| `dump_after_silence` | ≥ 4-day gap, then ≥ 5 commits within 20 minutes | 1 |
| `history_rewrite` | ≥ 3 commits authored > 24h before they landed | 0 |

CI streaks count *distinct commits*, not runs, so one flaky workflow re-run
does not inflate the streak. A run of "fix", "fix again", "revert" is the
clearest text signal that someone is circling a problem.

`dump_after_silence` and `history_rewrite` are the closest read available on
"worked locally and struggled, then force-pushed" — the Events API does not
expose force-push reliably, so these infer it from commit timestamps. Treat
them as suggestive, and note the report words them as observed timing, not as
a claim about what the participant did.

## Claude Code usage (bonus)

| Flag | Trigger | Severity |
| --- | --- | --- |
| `claude_md_evolving` | `CLAUDE.md` revised ≥ 3 times | 0 |
| `claude_md_static` | `CLAUDE.md` present but written once | 0 |

Always severity 0, in both directions. Not everyone commits `.claude/`
artifacts, and absence says nothing about how someone is working. A
`CLAUDE.md` that keeps getting revised is a good sign that project
understanding is growing; it is never held against anyone that it isn't there.

## Lines of code

A rough complexity proxy shown in each participant's headline, not a signal —
it never contributes to status. Only code counts; docs, lockfiles, minified
bundles and vendored directories are excluded, and the extension list with its
bytes-per-line divisors lives in `BYTES_PER_LINE` in `scripts/collect.py`.

| Mode | How | Cost | Shown as |
| --- | --- | --- | --- |
| `estimate` (default) | Blob sizes from the tree ÷ bytes-per-line for the extension | Free — the tree is already fetched | `~1,240 lines of code` |
| `exact` | Fetches each code blob and counts newlines | One API call per code file | `1,240 lines of code` |
| `off` | — | — | omitted |

A trailing `+` means the count is partial: the tree was truncated, or the
per-repo file cap (250) or size cap (400 KB) was hit.

Estimates land within roughly 5% on typical training repos. Verified against a
real repo during development: estimate 165, exact 174, `git` ground truth 174.

## README pill

`HAS README` (green) or `NO README` (red), from a top-level file matching
`readme(.*)`. Presence only — substance feeds the depth score instead.

## Depth of thinking

`DEEP` (green) / `MEDIUM` (blue) / `LIGHT` (grey), from a points total in
`_depth_score` in `scripts/analyze.py`. **It never changes a participant's
status.** It describes the character of the work, not whether someone is stuck.

| Contributor | Points |
| --- | --- |
| README present | 8 |
| README ≥ 1200 bytes (not a stub) | +8 |
| `CLAUDE.md` present | 8 |
| `CLAUDE.md` revised ≥ 3 times | +8 |
| Committed `.claude/` artefacts | 6 |
| Tests present | 14 |
| ≥ 2 supporting docs beyond the README | 8 |
| CI configured | 6 |
| ≥ 3 top-level modules (2 scores 4) | 8 |
| ≥ 10 code files | 4 |
| Mean commit subject ≥ 32 chars and < 30% placeholders | 12 |
| ≥ 15% of commits have a message body | 8 |

`DEEP` at ≥ 55, `MEDIUM` at ≥ 30, `LIGHT` below that.

The weighting is deliberate: tests and considered commit messages carry the
most, because both are hard to produce without actually understanding the
problem. No single artefact makes a repo deep — a `CLAUDE.md` alone gets you
16 of 55.

Read `LIGHT` as "early" rather than "bad". It earns attention when it persists
while commit volume climbs: that pattern is code arriving faster than
understanding.

## Structural basics

| Flag | Trigger | Severity |
| --- | --- | --- |
| `thin_structure` | No README and/or no tests, once there is any commit activity | 0 |

Suppressed for repos younger than `new_repo_grace_days` (3).

## Run-over-run change

Only fires when a previous snapshot exists for the org.

| Flag | Trigger | Severity |
| --- | --- | --- |
| `no_movement_since_last_run` | HEAD sha is unchanged since the last scan | 1 |
| `ci_still_failing` | CI was already failing last run and still is | 1 |

These exist so a second scan reports *change*. Something already flagged that
has not moved is a stronger call to act than the same signal seen once.

## Window selection

- `--since` / `--until` win if given, then `--days`.
- Otherwise: since the last snapshot, clamped to 7–30 days.
- The floor matters: if you scan two days in a row, a 2-day window has no
  usable cadence or trend signal, so it is widened to 7.
- The previous window used for trend comparison is always the same length,
  immediately before the current one.
