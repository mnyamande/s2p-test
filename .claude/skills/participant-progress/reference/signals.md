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
