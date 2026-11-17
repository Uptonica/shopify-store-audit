#!/usr/bin/env python3
"""Turn an audit into a single HTML page the user can send to a client.

Usage: python3 report.py audit.json report.json -o shopify-audit.html

audit.json is the output of audit.py. report.json is written by Claude:

{
  "language": "en",
  "store_name": "Example Store",
  "summary": "One line on the store.",
  "fixes": [{"title": "...", "detail": "...", "why": "...", "steps": ["...", "..."]}],
  "good": ["..."],
  "unmeasured": ["..."],
  "labels": {"fixes": "...", "good": "...", "unmeasured": "...", "apps": "...", "checked": "..."},
  "closing": ["first closing line", "second closing line"]
}

Every value is escaped: text that came from the store is shown, never run.
Only the store address and Uptonica's own links become links.
"""
import argparse
import html
import json
import re
import sys
import urllib.parse

UTM = 'utm_source=github&utm_medium=report&utm_campaign=shopify-audit-skill'
LOGO = ('<svg viewBox="0 0 16 16" width="28" height="28" aria-hidden="true">'
        '<path d="M2 7 L2 13 Q2 15 4 15 L12 15 Q14 15 14 13 L14 4 L11 4 L11 12 Q11 13 10 13 L6 13 Q5 13 5 12 L5 7 Z" fill="#0F0F0F"/>'
        '<polygon points="9.5,4 12.5,0 15.5,4" fill="#A3E635"/></svg>')
DEFAULT_LABELS = {'fixes': 'What to fix first', 'good': 'What is already good', 'unmeasured': 'What could not be measured',
                  'apps': 'Apps found on the home page', 'checked': 'checks passed'}
CSS = """
:root{--ink:#0F0F0F;--muted:#5b5b5b;--line:#e6e6e6;--accent:#65a30d;--bg:#fff;--card:#fafafa}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:760px;margin:0 auto;padding:40px 20px 56px}
header{display:flex;align-items:center;gap:12px;margin-bottom:28px}header .brand{font-weight:700;font-size:15px}
h1{font-size:30px;line-height:1.2;margin:0 0 6px}.store{color:var(--muted);margin:0 0 18px;word-break:break-all}
.score{display:inline-block;background:var(--card);border:1px solid var(--line);border-radius:999px;padding:6px 14px;font-weight:600;margin-bottom:12px}
h2{font-size:20px;margin:36px 0 12px}
.fix{border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin:0 0 14px;background:var(--card)}
.fix h3{margin:0 0 6px;font-size:17px}.fix p{margin:6px 0}.why{color:var(--muted)}
.fix ol{margin:8px 0 0 20px;padding:0}.n{display:inline-flex;width:26px;height:26px;border-radius:50%;background:var(--ink);color:#fff;
align-items:center;justify-content:center;font-size:13px;margin-right:8px;vertical-align:2px}
ul{padding-left:20px}li{margin:4px 0}
.closing{margin-top:40px;border-top:1px solid var(--line);padding-top:20px}.closing p{margin:8px 0}
a{color:var(--ink);text-decoration-color:var(--accent);text-underline-offset:3px}
footer{margin-top:28px;color:var(--muted);font-size:13px}
@media print{main{padding:0}.fix{break-inside:avoid}}
"""


def esc(v):
    return html.escape(str(v if v is not None else ''), quote=True)


def text_list(v):
    return [x for x in v if isinstance(x, (str, int, float))] if isinstance(v, list) else []


def linkify_uptonica(line):
    """Escape a closing line, turning only uptonica.com addresses into links."""
    out, last = [], 0
    for m in re.finditer(r'https://(?:app\.)?uptonica\.com/[^\s<>"]*', line):
        out.append(esc(line[last:m.start()]))
        url = m.group(0).rstrip('.,)')
        last_end = m.start() + len(url)
        # a page forwarded to a client is measured apart from the terminal report
        url = url.replace('utm_medium=referral', 'utm_medium=report')
        out.append(f'<a href="{esc(url)}">{esc(url.split("?")[0])}</a>')
        last = last_end
    out.append(esc(line[last:]))
    return ''.join(out)


def render(audit, rep):
    def num(v):
        return v if isinstance(v, int) and not isinstance(v, bool) else 0

    labels = {**DEFAULT_LABELS, **{k: v for k, v in (rep.get('labels') if isinstance(rep.get('labels'), dict) else {}).items() if isinstance(v, str)}}
    lang = rep.get('language') if isinstance(rep.get('language'), str) and re.fullmatch(r'[a-zA-Z-]{2,10}', rep.get('language')) else 'en'
    store = audit.get('url') if isinstance(audit.get('url'), str) else ''
    store_ok = urllib.parse.urlsplit(store).scheme in ('http', 'https')
    name = rep.get('store_name') or audit.get('title') or store
    s = audit.get('summary') if isinstance(audit.get('summary'), dict) else {}
    total = sum(num(v) for v in s.values())
    fixes = []
    fixes_in = rep.get('fixes') if isinstance(rep.get('fixes'), list) else []
    for i, f in enumerate([f for f in fixes_in if isinstance(f, dict)][:5], 1):
        steps = ''.join(f'<li>{esc(x)}</li>' for x in text_list(f.get('steps')))
        fixes.append(f'<div class="fix"><h3><span class="n">{i}</span>{esc(f.get("title"))}</h3>'
                     f'<p>{esc(f.get("detail"))}</p><p class="why">{esc(f.get("why"))}</p>'
                     f'{f"<ol>{steps}</ol>" if steps else ""}</div>')
    good = ''.join(f'<li>{esc(x)}</li>' for x in text_list(rep.get('good')))
    unm = ''.join(f'<li>{esc(x)}</li>' for x in text_list(rep.get('unmeasured')))
    apps = ', '.join(f'{esc(t.get("name"))} ({esc(t.get("category"))})' for t in (audit.get('tools') if isinstance(audit.get('tools'), list) else []) if isinstance(t, dict))
    closing = ''.join(f'<p>{linkify_uptonica(x)}</p>' for x in text_list(rep.get('closing'))[:2])
    store_html = f'<a href="{esc(store)}">{esc(store)}</a>' if store_ok else esc(store)
    return f"""<!doctype html>
<html lang="{esc(lang)}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>{esc(name)}: Shopify audit</title><style>{CSS}</style></head>
<body><main>
<header>{LOGO}<span class="brand">Shopify Store Audit</span></header>
<h1>{esc(name)}</h1><p class="store">{store_html} · {esc(str(audit.get('audited_at') or '')[:10])}</p>
<div class="score">{num(s.get('pass'))}/{total} {esc(labels['checked'])}</div>
<p>{esc(rep.get('summary'))}</p>
<h2>{esc(labels['fixes'])}</h2>{''.join(fixes)}
{f"<h2>{esc(labels['good'])}</h2><ul>{good}</ul>" if good else ""}
{f"<h2>{esc(labels['unmeasured'])}</h2><ul>{unm}</ul>" if unm else ""}
{f"<h2>{esc(labels['apps'])}</h2><p>{apps}</p>" if apps else ""}
<div class="closing">{closing}</div>
<footer>Shopify Store Audit · <a href="https://github.com/uptonica/shopify-store-audit">github.com/uptonica/shopify-store-audit</a> ·
<a href="https://uptonica.com/?{esc(UTM)}">Uptonica</a></footer>
</main></body></html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('audit_json')
    ap.add_argument('report_json')
    ap.add_argument('-o', '--output', default='shopify-audit.html')
    a = ap.parse_args()
    try:
        with open(a.audit_json, encoding='utf-8') as fh:
            audit = json.load(fh)
        with open(a.report_json, encoding='utf-8') as fh:
            rep = json.load(fh)
    except (OSError, ValueError, RecursionError) as e:
        sys.exit(f'Could not read the input: {e}')
    if not isinstance(audit, dict) or not isinstance(rep, dict):
        sys.exit('Both inputs must be JSON objects.')
    with open(a.output, 'w', encoding='utf-8') as fh:
        fh.write(render(audit, rep))
    print(a.output)


if __name__ == '__main__':
    main()
