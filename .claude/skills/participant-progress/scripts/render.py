#!/usr/bin/env python3
"""Render a scan report as Markdown and as a self-contained HTML page."""

import html
import json
from datetime import datetime, timezone

STATUS_ORDER = ["Needs intervention", "Watch", "On track"]
STATUS_KEY = {"Needs intervention": "intervene", "Watch": "watch", "On track": "ontrack"}
PRIVACY_NOTE = (
    "Participants should be told their repos are monitored for progress support. "
    "This tool reads public activity only and notifies no one — telling the cohort is on you."
)
SPARK = " ▁▂▃▄▅▆▇█"


def _fmt_day(value):
    if not value:
        return "unknown"
    try:
        dt = datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return value[:10]
    return dt.strftime("%b %-d")


def sparkline(daily):
    if not daily or not any(daily):
        return ""
    peak = max(daily) or 1
    return "".join(SPARK[min(len(SPARK) - 1, int(round(v / peak * (len(SPARK) - 1))))] for v in daily)


def _headline(entry):
    """The one-glance numeric summary that sits under a participant's name."""
    bits = []
    n, prev = entry.get("commits", 0), entry.get("commits_prev", 0)
    bits.append("%d commit%s in window" % (n, "" if n == 1 else "s"))
    if prev:
        bits.append("%d previous" % prev)
    days = entry.get("days_since_last_commit")
    if days is not None:
        bits.append("last commit %s" % ("today" if days == 0 else
                                        "yesterday" if days == 1 else "%d days ago" % days))
    return " · ".join(bits)


# -- Markdown --------------------------------------------------------------

def to_markdown(report):
    meta = report["meta"]
    entries = report["participants"]
    counts = {s: sum(1 for e in entries if e["status"] == s) for s in STATUS_ORDER}
    out = []
    out.append("# Cohort progress — %s" % meta["org"])
    sub = ["**%s – %s** (%d days)" % (_fmt_day(meta["since"]), _fmt_day(meta["until"]), meta["window_days"]),
           "%d participant%s / %d repo%s" % (len(entries), "" if len(entries) == 1 else "s",
                                             meta["repos_scanned"], "" if meta["repos_scanned"] == 1 else "s")]
    if meta.get("compared_to"):
        sub.append("compared against the %s run" % _fmt_day(meta["compared_to"]))
    else:
        sub.append("first run — no prior snapshot to compare")
    out.append("_%s_" % " · ".join(sub))
    out.append("")
    out.append("**%d need%s intervention · %d to watch · %d on track**"
               % (counts["Needs intervention"], "s" if counts["Needs intervention"] == 1 else "",
                  counts["Watch"], counts["On track"]))
    out.append("")

    for status in STATUS_ORDER:
        group = [e for e in entries if e["status"] == status]
        if not group:
            continue
        out.append("## %s (%d)" % (status, len(group)))
        out.append("")
        if status == "On track":
            for entry in group:
                line = "- **%s** — `%s` — %s" % (
                    entry["participant"], ", ".join(r.split("/")[-1] for r in entry["repos"]),
                    entry["evidence"][0] if entry["evidence"] else _headline(entry))
                out.append(line)
            out.append("")
            continue
        for entry in group:
            out.append("### %s — `%s`" % (entry["participant"],
                                          ", ".join(r.split("/")[-1] for r in entry["repos"])))
            head = _headline(entry)
            if entry.get("status_change"):
                head += " · _%s_" % entry["status_change"]
            out.append("%s  " % head)
            spark = sparkline(entry.get("daily") or [])
            if spark:
                out.append("`%s` commits/day across the window" % spark)
            out.append("")
            for item in entry["evidence"]:
                out.append("- %s" % item)
            if entry.get("angle"):
                out.append("")
                out.append("**Angle:** %s" % entry["angle"])
            out.append("")

    if report.get("unmapped"):
        out.append("## Name inferred from repo (%d)" % len(report["unmapped"]))
        out.append("")
        out.append("Analysed under a name inferred from the repo name — they matched neither "
                   "the roster nor the naming convention. Add them to the roster CSV to fix the label:")
        out.append("")
        for repo in report["unmapped"]:
            out.append("- `%s`" % repo)
        out.append("")

    out.append("---")
    out.append("")
    notes = ["Scanned %d repo%s" % (meta["repos_scanned"], "" if meta["repos_scanned"] == 1 else "s")]
    if meta.get("repos_skipped"):
        notes.append("skipped %d (archived/forks/empty)" % meta["repos_skipped"])
    notes.append("%d API call%s" % (meta.get("api_calls", 0), "" if meta.get("api_calls") == 1 else "s"))
    notes.append("via `%s`" % meta.get("backend", "rest"))
    out.append("_%s._" % " · ".join(notes))
    out.append("")
    snap = meta.get("snapshot_path", "n/a")
    out.append("_Snapshot: `%s`%s_" % (snap, "" if snap.startswith("not written")
                                       else " — the next run diffs against it."))
    out.append("")
    out.append("> **Note:** %s" % PRIVACY_NOTE)
    return "\n".join(out)


# -- HTML ------------------------------------------------------------------

CSS = """
:root{color-scheme:light dark;--bg:#fbfbfa;--card:#fff;--fg:#1c1c1a;--muted:#6b6b66;
--border:#e3e3df;--rule:#efefec;--accent:#3d5a80;
--intervene:#a8321f;--intervene-bg:#fbeae7;--watch:#8a5a10;--watch-bg:#fdf3e2;
--ontrack:#2f6b46;--ontrack-bg:#e9f4ee;--spark:#8f8f88;}
@media (prefers-color-scheme:dark){:root{--bg:#16171a;--card:#1e2024;--fg:#e8e8e4;
--muted:#9b9b95;--border:#31343a;--rule:#26282d;--accent:#9db8d8;
--intervene:#ff9c85;--intervene-bg:#3a201c;--watch:#ecc07a;--watch-bg:#332916;
--ontrack:#8fd3a8;--ontrack-bg:#1b2f23;--spark:#7d7d77;}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);line-height:1.55;
font-family:ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
-webkit-font-smoothing:antialiased}
main{max-width:820px;margin:0 auto;padding:2.5rem 1.25rem 4rem}
h1{font-size:1.6rem;margin:0 0 .35rem;letter-spacing:-.01em}
.sub{color:var(--muted);font-size:.85rem;margin:0 0 1.5rem}
.tally{display:flex;gap:.5rem;flex-wrap:wrap;margin:0 0 2rem}
.tally div{flex:1 1 150px;border:1px solid var(--border);border-radius:10px;
padding:.7rem .85rem;background:var(--card)}
.tally b{display:block;font-size:1.5rem;line-height:1.1}
.tally span{font-size:.75rem;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.tally .intervene b{color:var(--intervene)}.tally .watch b{color:var(--watch)}
.tally .ontrack b{color:var(--ontrack)}
h2{font-size:.78rem;text-transform:uppercase;letter-spacing:.09em;color:var(--muted);
margin:2.2rem 0 .8rem;padding-bottom:.4rem;border-bottom:1px solid var(--rule)}
.card{background:var(--card);border:1px solid var(--border);border-left-width:3px;
border-radius:10px;padding:1rem 1.1rem;margin:0 0 .8rem}
.card.intervene{border-left-color:var(--intervene)}
.card.watch{border-left-color:var(--watch)}
.card.ontrack{border-left-color:var(--ontrack);padding:.65rem 1.1rem}
.top{display:flex;align-items:baseline;gap:.6rem;flex-wrap:wrap}
.name{font-weight:650;font-size:1.02rem}
.chip{font-size:.68rem;font-weight:600;text-transform:uppercase;letter-spacing:.05em;
padding:.15rem .5rem;border-radius:999px;white-space:nowrap}
.chip.intervene{color:var(--intervene);background:var(--intervene-bg)}
.chip.watch{color:var(--watch);background:var(--watch-bg)}
.chip.ontrack{color:var(--ontrack);background:var(--ontrack-bg)}
a.repo{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.78rem;
color:var(--accent);text-decoration:none}
a.repo:hover{text-decoration:underline}
.head{color:var(--muted);font-size:.8rem;margin:.3rem 0 .5rem}
.change{color:var(--watch);font-style:italic}
ul{margin:.5rem 0 0;padding-left:1.1rem}
li{margin:.28rem 0;font-size:.9rem}
.angle{margin:.75rem 0 0;padding:.5rem .7rem;background:var(--bg);border-radius:7px;
font-size:.86rem}
.angle b{color:var(--accent)}
.sparkwrap{display:flex;align-items:flex-end;gap:.5rem;margin:.55rem 0 .15rem}
.spark{display:flex;align-items:flex-end;gap:3px;height:24px;
padding-bottom:3px;border-bottom:1px solid var(--border)}
.spark i{display:block;width:7px;min-height:3px;background:var(--spark);
border-radius:1.5px 1.5px 0 0}
.spark i.z{height:3px!important;opacity:.3}
.sparklabel{font-size:.68rem;color:var(--muted);padding-bottom:.15rem}
footer{margin-top:2.5rem;padding-top:1rem;border-top:1px solid var(--rule);
color:var(--muted);font-size:.78rem}
footer code{font-size:.75rem;word-break:break-all}
.note{margin-top:.8rem;padding:.6rem .8rem;border:1px dashed var(--border);border-radius:8px}
@media print{body{background:#fff}.card{break-inside:avoid}}
"""


def _spark_html(daily):
    if not daily or not any(daily):
        return ""
    peak = max(daily) or 1
    bars = "".join(
        '<i class="%s" style="height:%d%%" title="%d commit(s)"></i>'
        % ("z" if v == 0 else "", max(12, int(v / peak * 100)), v) for v in daily
    )
    return ('<div class="sparkwrap"><div class="spark" aria-label="commits per day">%s</div>'
            '<span class="sparklabel">commits/day · peak %d</span></div>' % (bars, peak))


def to_html(report):
    meta = report["meta"]
    entries = report["participants"]
    counts = {s: sum(1 for e in entries if e["status"] == s) for s in STATUS_ORDER}
    esc = html.escape
    p = []
    p.append("<!doctype html><html lang='en'><head><meta charset='utf-8'>")
    p.append("<meta name='viewport' content='width=device-width,initial-scale=1'>")
    p.append("<title>Cohort progress — %s</title><style>%s</style></head><body><main>" % (esc(meta["org"]), CSS))
    p.append("<h1>Cohort progress — %s</h1>" % esc(meta["org"]))
    sub = "%s – %s (%d days) · %d participants / %d repos · %s" % (
        _fmt_day(meta["since"]), _fmt_day(meta["until"]), meta["window_days"],
        len(entries), meta["repos_scanned"],
        ("compared against the %s run" % _fmt_day(meta["compared_to"]))
        if meta.get("compared_to") else "first run — no prior snapshot")
    p.append("<p class='sub'>%s</p>" % esc(sub))

    p.append("<div class='tally'>")
    for status in STATUS_ORDER:
        p.append("<div class='%s'><b>%d</b><span>%s</span></div>"
                 % (STATUS_KEY[status], counts[status], esc(status)))
    p.append("</div>")

    for status in STATUS_ORDER:
        group = [e for e in entries if e["status"] == status]
        if not group:
            continue
        key = STATUS_KEY[status]
        p.append("<h2>%s — %d</h2>" % (esc(status), len(group)))
        for entry in group:
            p.append("<div class='card %s'>" % key)
            p.append("<div class='top'><span class='name'>%s</span>" % esc(entry["participant"]))
            p.append("<span class='chip %s'>%s</span>" % (key, esc(status)))
            for repo, url in zip(entry["repos"], entry.get("urls") or []):
                label = repo.split("/")[-1]
                p.append("<a class='repo' href='%s'>%s</a>" % (esc(url or "#"), esc(label)))
            p.append("</div>")
            head = esc(_headline(entry))
            if entry.get("status_change"):
                head += " · <span class='change'>%s</span>" % esc(entry["status_change"])
            p.append("<div class='head'>%s</div>" % head)
            if status == "On track":
                if entry["evidence"]:
                    p.append("<div class='head'>%s</div>" % esc(entry["evidence"][0]))
            else:
                p.append(_spark_html(entry.get("daily") or []))
                p.append("<ul>%s</ul>" % "".join("<li>%s</li>" % esc(x) for x in entry["evidence"]))
                if entry.get("angle"):
                    p.append("<div class='angle'><b>Angle:</b> %s</div>" % esc(entry["angle"]))
            p.append("</div>")

    if report.get("unmapped"):
        p.append("<h2>Name inferred from repo — %d</h2>" % len(report["unmapped"]))
        p.append("<div class='card'><div class='head'>Analysed under a name inferred from the "
                 "repo name — matched neither the roster nor the naming convention. "
                 "Add them to the roster CSV to fix the label.</div><ul>%s</ul></div>"
                 % "".join("<li><code>%s</code></li>" % esc(r) for r in report["unmapped"]))

    notes = ["Scanned %d repos" % meta["repos_scanned"]]
    if meta.get("repos_skipped"):
        notes.append("skipped %d (archived/forks/empty)" % meta["repos_skipped"])
    notes.append("%d API calls via %s" % (meta.get("api_calls", 0), meta.get("backend", "rest")))
    notes.append("generated %s" % datetime.now(timezone.utc).strftime("%b %-d %H:%M UTC"))
    p.append("<footer><div>%s.</div>" % esc(" · ".join(notes)))
    p.append("<div>Snapshot: <code>%s</code></div>" % esc(meta.get("snapshot_path", "n/a")))
    p.append("<div class='note'><b>Note:</b> %s</div></footer>" % esc(PRIVACY_NOTE))
    p.append("</main></body></html>")
    return "".join(p)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Render a participant-progress scan report.")
    ap.add_argument("report", help="JSON report produced by scan.py")
    ap.add_argument("--md", help="write Markdown here")
    ap.add_argument("--html", help="write HTML here")
    args = ap.parse_args()
    with open(args.report) as fh:
        report = json.load(fh)
    md = to_markdown(report)
    if args.md:
        with open(args.md, "w") as fh:
            fh.write(md + "\n")
    if args.html:
        with open(args.html, "w") as fh:
            fh.write(to_html(report))
    if not args.md and not args.html:
        print(md)


if __name__ == "__main__":
    main()
