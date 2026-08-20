# agentic-ai-coop cohort config

Generated from the live org listing on 2026-08-20: **82 repos, 75 of them
participant repos, mapping to 74 participants.**

| File | What it is |
| --- | --- |
| `cohort-repos.txt` | The 75 participant repos, one per line. Feed to `--repos-from` when org-wide listing isn't available (i.e. inside a cloud session). |
| `roster.csv` | The 13 repos the `s2p-firstname-lastname` convention gets wrong. Everything else maps correctly from its name. |

## Running the scan

Locally, where `gh` can enumerate the org — nothing to maintain:

```bash
python3 .claude/skills/participant-progress/scripts/scan.py \
  --org agentic-ai-coop \
  --roster cohort/roster.csv \
  --exclude '^(s2p-materials|s2p-skills|s2p-delivery|s2p-cohort-board|S2P_WORKINGS|.*-template)$' \
  --md /tmp/cohort.md --html /tmp/cohort.html
```

From a cloud session, where org listing is blocked, use the explicit list:

```bash
python3 .claude/skills/participant-progress/scripts/scan.py \
  --org agentic-ai-coop \
  --repos-from cohort/cohort-repos.txt \
  --roster cohort/roster.csv \
  --md /tmp/cohort.md --html /tmp/cohort.html
```

## Excluded as infrastructure, not participant work

`s2p-materials`, `s2p-skills`, `s2p-delivery`, `s2p-strategy-template`,
`s2p-participant-template`, `s2p-cohort-board`, `S2P_WORKINGS`.

Move any of these into `cohort-repos.txt` if you'd rather track them.

## Two roster rows need your confirmation

`s2p-MyWordsmith` and `s2p-Blesing_Wk2` don't carry enough in the repo name to
identify the participant, so they're marked `CONFIRM` in `roster.csv` rather
than guessed at. Until you fix them the report still includes both — just under
a placeholder label.

## One participant has two repos

`s2p-henk-van-huyssteen` and `SP2-HvanHuyssteen_data-strategy` both belong to
Henk van Huyssteen. The roster maps both to the same name and the scan merges
them into a single briefing entry, taking the worst status across the two.
