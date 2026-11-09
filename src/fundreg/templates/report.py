"""HTML reporting for findings produced anywhere in the toolkit.

One renderer serves the UCITS breach report and the EET/EMT validation report,
because both are the same object: a list of graded findings with an audit trail.
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from fundreg.findings import FindingSet, Severity

_BADGE = {
    Severity.PASS: ("Pass", "ok"),
    Severity.WARNING: ("Warning", "warn"),
    Severity.BREACH: ("Breach", "bad"),
    Severity.INCONCLUSIVE: ("No data", "none"),
}

_CSS = """
:root{--ink:#131920;--muted:#616d7e;--line:#dde3eb;--bg:#fff;--panel:#f5f8fc;
--ok:#1d6b45;--warn:#9a6212;--bad:#9a1f2e;--none:#596273;--accent:#1f4e79}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){
--ink:#e7ecf3;--muted:#9aa6b8;--line:#2a3340;--bg:#131a23;--panel:#19222d;
--ok:#56c08b;--warn:#e0a94b;--bad:#f2798c;--none:#8a95a6;--accent:#7fb3e3}}
:root[data-theme=dark]{--ink:#e7ecf3;--muted:#9aa6b8;--line:#2a3340;--bg:#131a23;
--panel:#19222d;--ok:#56c08b;--warn:#e0a94b;--bad:#f2798c;--none:#8a95a6;--accent:#7fb3e3}
*{box-sizing:border-box}
body{margin:0;padding:28px 16px;background:var(--bg);color:var(--ink);
font:15px/1.55 "Segoe UI",system-ui,-apple-system,sans-serif}
main{max-width:1040px;margin:0 auto}
h1{font-size:23px;margin:0 0 2px;letter-spacing:-.01em}
.sub{color:var(--muted);font-size:13.5px;margin:0 0 20px}
.cards{display:flex;flex-wrap:wrap;gap:10px;margin:0 0 22px}
.card{flex:1 1 140px;background:var(--panel);border:1px solid var(--line);
border-radius:9px;padding:13px 15px}
.card b{display:block;font-size:26px;line-height:1.15;font-variant-numeric:tabular-nums}
.card span{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.card.ok b{color:var(--ok)}.card.warn b{color:var(--warn)}
.card.bad b{color:var(--bad)}.card.none b{color:var(--none)}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th,td{border-bottom:1px solid var(--line);padding:9px 10px;text-align:left;vertical-align:top}
th{background:var(--panel);font-weight:600;position:sticky;top:0}
td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.badge{display:inline-block;padding:2px 9px;border-radius:999px;font-size:11.5px;
font-weight:600;white-space:nowrap}
.badge.ok{background:color-mix(in srgb,var(--ok) 15%,transparent);color:var(--ok)}
.badge.warn{background:color-mix(in srgb,var(--warn) 18%,transparent);color:var(--warn)}
.badge.bad{background:color-mix(in srgb,var(--bad) 16%,transparent);color:var(--bad)}
.badge.none{background:color-mix(in srgb,var(--none) 16%,transparent);color:var(--none)}
.rule{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:11.5px;
color:var(--muted);display:block;margin-top:2px}
.msg{color:var(--muted);font-size:12.5px;margin-top:3px}
.filters{display:flex;gap:8px;margin:0 0 12px;flex-wrap:wrap}
.filters button{border:1px solid var(--line);background:var(--panel);color:var(--ink);
border-radius:999px;padding:5px 14px;font:inherit;font-size:12.5px;cursor:pointer}
.filters button[aria-pressed=true]{background:var(--accent);border-color:var(--accent);color:#fff}
.foot{margin-top:26px;color:var(--muted);font-size:12px}
@media (max-width:640px){th:nth-child(5),td:nth-child(5){display:none}}
"""

_JS = """
const buttons=document.querySelectorAll('.filters button');
buttons.forEach(b=>b.addEventListener('click',()=>{
  const want=b.dataset.sev;
  buttons.forEach(o=>o.setAttribute('aria-pressed',String(o===b)));
  document.querySelectorAll('tbody tr').forEach(r=>{
    r.hidden = want!=='all' && r.dataset.sev!==want;
  });
}));
"""


def _esc(value) -> str:
    return html.escape("" if value is None else str(value))


def render_findings_html(
    findings: FindingSet,
    *,
    title: str,
    subtitle: str = "",
) -> str:
    """Render a finding set as a standalone, filterable HTML report."""

    counts = findings.counts()
    cards = "".join(
        f'<div class="card {css}"><b>{counts[sev.value]}</b><span>{label}</span></div>'
        for sev, (label, css) in _BADGE.items()
    )

    rows = []
    for f in findings.sorted_by_severity():
        label, css = _BADGE[f.severity]
        actual = "" if f.actual is None else f"{f.actual:,.2f}"
        limit = "" if f.limit is None else f"{f.limit:,.2f}"
        headroom = "" if f.headroom is None else f"{f.headroom:,.2f}"
        rows.append(
            f'<tr data-sev="{f.severity.value}">'
            f'<td><span class="badge {css}">{label}</span></td>'
            f"<td>{_esc(f.subject)}</td>"
            f'<td>{_esc(f.title)}<span class="rule">{_esc(f.rule_id)} &middot; '
            f'{_esc(f.reference)}</span><div class="msg">{_esc(f.message)}</div></td>'
            f'<td class="num">{actual}</td>'
            f'<td class="num">{limit}</td>'
            f'<td class="num">{headroom}</td>'
            f"</tr>"
        )

    unit = next((f.unit for f in findings if f.limit is not None), "")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)}</title><style>{_CSS}</style></head><body>
<main>
  <h1>{_esc(title)}</h1>
  <p class="sub">{_esc(subtitle)}{" &middot; " if subtitle else ""}
     {_esc(findings.summary_line())}</p>
  <div class="cards">{cards}</div>
  <div class="filters">
    <button data-sev="all" aria-pressed="true">All</button>
    <button data-sev="breach" aria-pressed="false">Breaches</button>
    <button data-sev="warning" aria-pressed="false">Warnings</button>
    <button data-sev="inconclusive" aria-pressed="false">No data</button>
    <button data-sev="pass" aria-pressed="false">Passes</button>
  </div>
  <table><thead><tr>
    <th>Outcome</th><th>Subject</th><th>Rule</th>
    <th class="num">Actual</th><th class="num">Limit</th><th class="num">Headroom</th>
  </tr></thead><tbody>{"".join(rows)}</tbody></table>
  <p class="foot">Figures in {_esc(unit) or "the unit of each rule"}.
     Generated {datetime.now().strftime("%Y-%m-%d %H:%M")} by fund-regdata-toolkit.
     A demonstration of the methodology, not a regulatory filing.</p>
</main>
<script>{_JS}</script></body></html>
"""


def write_findings_html(
    findings: FindingSet, path: str | Path, *, title: str, subtitle: str = ""
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_findings_html(findings, title=title, subtitle=subtitle), encoding="utf-8"
    )
    return path
