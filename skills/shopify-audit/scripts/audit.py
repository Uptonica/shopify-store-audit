#!/usr/bin/env python3
"""Read-only audit of a public Shopify storefront.

Usage: python3 audit.py https://store.example [--max-products 1000] [--samples 5]

Prints one JSON document on stdout. Every check carries a status
(pass, fail, warn, unmeasured) and the evidence it was decided on, so the
report can quote facts instead of guessing. Standard library only; it reads
public pages at a polite pace (one request every 1.5 seconds or slower) and never logs
in, posts or changes anything.
"""
import argparse
import csv
import html
import ipaddress
import json
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = 'Mozilla/5.0 (compatible; shopify-store-audit/0.1; +https://github.com/uptonica/shopify-store-audit)'
PAUSE = 1.5

TOOLS = {
    'Klaviyo': ('email', ['klaviyo.com', 'klaviyo.js']),
    'Mailchimp': ('email', ['chimpstatic.com', 'list-manage.com']),
    'Omnisend': ('email', ['omnisrc.com', 'omnisend.com']),
    'ActiveCampaign': ('email', ['activehosted.com', 'trackcmp.net']),
    'Brevo': ('email', ['sibautomation.com', 'sendinblue.com', 'brevo.com']),
    'Drip': ('email', ['getdrip.com']),
    'Skeepers': ('reviews', ['skeepers.io', 'netreviews.com']),
    'Yotpo': ('reviews', ['yotpo.com']),
    'Judge.me': ('reviews', ['judge.me']),
    'Loox': ('reviews', ['loox.io', 'looxcdn.com']),
    'Stamped': ('reviews', ['stamped.io', 'cdn-stamped-io']),
    'Okendo': ('reviews', ['okendo.io', 'cdn-static.okendo']),
    'Trustpilot': ('reviews', ['widget.trustpilot.com', 'invitejs.trustpilot', 'ecommplugins-scripts.trustpilot']),
    'Smile.io': ('loyalty', ['smile.io']),
    'LoyaltyLion': ('loyalty', ['loyaltylion.com', 'loyaltylion.net']),
    'Privy': ('popups', ['privy.com']),
    'Justuno': ('popups', ['justuno.com', 'cdn.jst.ai']),
    'OptinMonster': ('popups', ['optinmonster.com', 'a.optnmnstr.com']),
    'Hotjar': ('analytics', ['hotjar.com']),
    'Lucky Orange': ('analytics', ['luckyorange.com']),
    'Triple Whale': ('analytics', ['triplewhale.com', 'triplewhale-pixel']),
    'PageFly': ('page builder', ['pagefly.io', 'cdn.pagefly']),
    'Shogun': ('page builder', ['getshogun.com', 'shogun-cdn']),
    'ReConvert': ('upsell', ['reconvert.io', 'reconvert-upsell']),
    'Rebuy': ('upsell', ['rebuyengine.com']),
    'Recharge': ('subscriptions', ['rechargecdn.com', 'rechargepayments.com']),
    'Searchanise': ('search', ['searchanise.com', 'searchserverapi.com']),
    'Algolia': ('search', ['algolia.net', 'algolianet.com']),
    'Klevu': ('search', ['klevu.com']),
    'AfterShip': ('shipping', ['aftership.com']),
    'Loop Returns': ('returns', ['loopreturns.com']),
}
CHAT = {
    'Intercom': ['widget.intercom.io', 'intercomSettings', 'intercomcdn.com'],
    'Zendesk': ['static.zdassets.com', 'zopim', 'ekr.zdassets.com'],
    'Tidio': ['tidio.co', 'tidiochat'],
    'Crisp': ['client.crisp.chat', '$crisp'],
    'Drift': ['js.driftt.com'],
    'LiveChat': ['cdn.livechatinc.com', '__lc.license'],
    'Tawk.to': ['embed.tawk.to', 'Tawk_API'],
    'Gorgias': ['config.gorgias.chat', 'gorgias.io'],
    'HubSpot': ['js.hs-scripts.com'],
    'Shopify Inbox': ['shopifycloud/chat', 'shopify-chat'],
    'Manychat': ['mcwidget'],
    'WhatsApp link': ['wa.me/', 'api.whatsapp.com/send'],
}
AI_BOTS = ['GPTBot', 'OAI-SearchBot', 'ChatGPT-User', 'ClaudeBot', 'Claude-SearchBot', 'PerplexityBot', 'Google-Extended']


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Re-check every redirect: a store must not send the script to a private address.

    DNS rebinding (the name resolving differently between check and connect)
    stays possible; acceptable for a tool the user runs on their own machine.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        p = urllib.parse.urlsplit(newurl)
        if p.scheme not in ('http', 'https') or not public_host(p.hostname or ''):
            raise urllib.error.HTTPError(newurl, 403, 'redirect to a non-public address blocked', headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


OPENER = urllib.request.build_opener(_SafeRedirect)
HEAD = 500_000  # regexes only look this far: head tags and JSON-LD sit near the top


class Fetcher:
    def __init__(self, base):
        self.base = base
        self.last = 0.0
        self.count = 0
        self.pause = PAUSE
        self.limited = False

    def get(self, path_or_url, accept='text/html'):
        url = path_or_url if path_or_url.startswith('http') else self.base + path_or_url
        wait = self.pause - (time.time() - self.last)
        if wait > 0:
            time.sleep(wait)
        self.last = time.time()
        self.count += 1
        req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': accept, 'Accept-Language': 'en;q=0.8,*;q=0.5'})
        for attempt in range(3):
            try:
                with OPENER.open(req, timeout=20) as r:
                    return r.status, r.read(3_000_000).decode('utf-8', 'replace'), r.geturl()
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    self.limited = True
                if e.code != 429 or attempt >= 1:
                    return e.code, '', url
                # Shopify rate limit: wait as asked (capped), then slow down for the rest of the run
                retry = e.headers.get('Retry-After') or '5'
                time.sleep(min(float(retry) if retry.replace('.', '', 1).isdigit() else 5, 10))
                self.pause = 3.0
            except (urllib.error.URLError, TimeoutError, socket.timeout) as e:
                return 0, str(e), url
        return 0, '', url


def check(cid, area, status, title, value=None, evidence=None):
    return {'id': cid, 'area': area, 'status': status, 'title': title, 'value': value, 'evidence': evidence}


def public_host(host):
    try:
        infos = socket.getaddrinfo(host, 443)
    except socket.gaierror:
        return False
    return all(ipaddress.ip_address(i[4][0]).is_global for i in infos)


def meta(page, name):
    page = page[:HEAD]
    for pat in (rf'<meta[^>]{{1,2000}}?(?:name|property)=["\']{name}["\'][^>]{{0,2000}}?content=["\']([^"\']{{0,5000}})',
                rf'<meta[^>]{{1,2000}}?content=["\']([^"\']{{0,5000}})["\'][^>]{{0,2000}}?(?:name|property)=["\']{name}["\']'):
        m = re.search(pat, page, re.I)
        if m:
            return html.unescape(m.group(1)).strip()
    return None


def jsonld(page):
    out = []
    for block in re.findall(r'<script[^>]{1,500}?application/ld\+json[^>]{0,500}>(.{0,200000}?)</script>', page[:HEAD], re.S | re.I):
        try:
            data = json.loads(block.strip())
        except (ValueError, RecursionError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                out.append(item)
                stack.extend(item.get('@graph', []) if isinstance(item.get('@graph'), list) else [])
    return out


def types(items):
    t = set()
    for i in items:
        v = i.get('@type')
        t.update(x for x in (v if isinstance(v, list) else [v]) if isinstance(x, str) and x)
    return t


def words(body):
    body = body if isinstance(body, str) else ''
    text = re.sub(r'<(script|style)\b.{0,20000}?</\1>', ' ', body[:HEAD], flags=re.S | re.I)
    text = html.unescape(re.sub(r'<[^>]{0,2000}>', ' ', text))
    return len(re.findall(r'\w+', text))


def robots_blocks(txt):
    """User agents whose group disallows the whole site."""
    blocked, agents, in_rules = set(), [], False
    for raw in txt.splitlines():
        line = raw.split('#')[0].strip()
        if ':' not in line:
            continue
        k, v = (s.strip() for s in line.split(':', 1))
        k = k.lower()
        if k == 'user-agent':
            if in_rules:
                agents, in_rules = [], False
            agents.append(v.lower())
        elif k in ('disallow', 'allow'):
            in_rules = True
            if k == 'disallow' and v == '/':
                blocked.update(agents)
    return blocked


def audit(url, max_products, samples):
    if not re.match(r'https?://', url):
        url = 'https://' + url
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ''
    if not host or not public_host(host):
        return {'url': url, 'error': 'The address does not resolve to a public website.'}
    f = Fetcher(f'{parts.scheme}://{host}')
    checks = []

    status, home, final = f.get('/')
    if status != 200 or not home:
        return {'url': url, 'error': f'The home page did not answer (HTTP {status}). It may be password-protected or blocking automated visits.'}
    f.base = '{0.scheme}://{0.netloc}'.format(urllib.parse.urlsplit(final))
    is_shopify = 'cdn.shopify.com' in home or 'Shopify.theme' in home
    if not is_shopify:
        checks.append(check('platform', 'store', 'warn', 'This does not look like a Shopify store; catalog checks are skipped.'))

    # Home page basics
    title = (re.search(r'<title[^>]{0,500}>(.{0,1000}?)</title>', home[:HEAD], re.S | re.I) or [None, None])[1]
    title = html.unescape(title).strip() if title else None
    desc = meta(home, 'description')
    checks.append(check('https', 'search', 'pass' if f.base.startswith('https://') else 'fail', 'Secure address (https)', f.base))
    checks.append(check('home_title', 'search', 'pass' if title and 15 <= len(title) <= 65 else ('fail' if not title else 'warn'),
                        'Home page title present and 15-65 characters', len(title) if title else 0, title))
    checks.append(check('home_description', 'search', 'pass' if desc and 70 <= len(desc) <= 160 else ('fail' if not desc else 'warn'),
                        'Home page meta description present and 70-160 characters', len(desc) if desc else 0, desc))
    og = meta(home, 'og:image')
    checks.append(check('share_image', 'search', 'pass' if og else 'fail', 'Preview image for shared links (og:image)', bool(og), og))
    canon = re.search(r'<link[^>]+rel=["\']canonical["\']', home, re.I)
    checks.append(check('canonical', 'search', 'pass' if canon else 'fail', 'Canonical address declared on the home page', bool(canon)))
    home_types = types(jsonld(home))
    org = bool(home_types & {'Organization', 'OnlineStore', 'Store', 'LocalBusiness'})
    checks.append(check('organization_data', 'ai', 'pass' if org else 'fail',
                        'Structured data that tells Google and AI assistants who the store is (Organization)', sorted(home_types)))

    # Languages
    langs = sorted(set(m.lower().split('-')[0] for m in re.findall(r'hreflang=["\']([a-zA-Z-]+)["\']', home) if m.lower() != 'x-default'))
    html_lang = (re.search(r'<html[^>]+lang=["\']([^"\']+)', home, re.I) or [None, None])[1]
    checks.append(check('languages', 'markets', 'pass' if len(langs) > 1 else 'warn',
                        'Store published in more than one language', langs or [html_lang],
                        'Only declared languages are counted; whether each page is really translated is not measured.'))

    # Robots and sitemap
    st, robots, _ = f.get('/robots.txt', 'text/plain')
    if st == 200:
        blocked = robots_blocks(robots)
        ai_blocked = [b for b in AI_BOTS if b.lower() in blocked or '*' in blocked]
        checks.append(check('ai_crawlers', 'ai', 'fail' if ai_blocked else 'pass',
                            'AI assistants (ChatGPT, Claude, Perplexity, Gemini) are allowed to read the store', ai_blocked or 'none blocked'))
        checks.append(check('sitemap_in_robots', 'search', 'pass' if re.search(r'^\s*sitemap:', robots, re.I | re.M) else 'warn',
                            'robots.txt points to the sitemap'))
    else:
        checks.append(check('ai_crawlers', 'ai', 'unmeasured', 'robots.txt could not be read', st))
    st, _, _ = f.get('/sitemap.xml', 'application/xml')
    checks.append(check('sitemap', 'search', 'pass' if st == 200 else 'fail', 'Sitemap available at /sitemap.xml', st))

    # Tools and chat
    low = home.lower()
    found = [{'name': n, 'category': c} for n, (c, needles) in TOOLS.items() if any(x.lower() in low for x in needles)]
    chat = next((n for n, needles in CHAT.items() if any(x.lower() in low for x in needles)), None)
    checks.append(check('chat', 'customers', 'pass' if chat else 'warn', 'A chat or messaging channel on the home page', chat,
                        'Read from the home page code; a chat loaded only after cookie consent may not be visible.'))

    catalog = {}
    if is_shopify:
        def read_page(page):
            st, body, _ = f.get(f'/products.json?limit=250&page={page}', 'application/json')
            if st != 200:
                return st, None
            try:
                data = json.loads(body)
            except (ValueError, RecursionError):
                return st, None
            items = data.get('products') if isinstance(data, dict) else None
            return st, [p for p in items if isinstance(p, dict) and isinstance(p.get('handle'), str)] if isinstance(items, list) else None

        st, batch = read_page(1)
        products = batch or []
        rows = []
        # Product pages: what Google and AI assistants actually read
        first = products[:250]
        picked = first[::max(1, len(first) // samples)][:samples]
        for p in picked:
            if f.limited:
                rows.append({'handle': p['handle'], 'photos': None, 'photos_without_alt': None, 'status': 429,
                             'url': f.base + '/products/' + p['handle']})
                continue
            jst, js, _ = f.get(f"/products/{p['handle']}.js", 'application/json')
            try:
                data = json.loads(js) if jst == 200 else None
                media = data.get('media') or [] if isinstance(data, dict) else None
            except (ValueError, RecursionError):
                media = None
            media = media if isinstance(media, list) else None
            pics = [m for m in media or [] if isinstance(m, dict) and m.get('media_type') == 'image']
            photo_row = {'handle': p['handle'],
                         'photos': len(pics) if media is not None else None,
                         'photos_without_alt': sum(1 for m in pics if not (m.get('alt') or '').strip()) if media is not None else None}
            st, pg, _ = f.get(f"/products/{p['handle']}")
            if st != 200:
                rows.append(photo_row | {'status': st, 'url': f.base + '/products/' + p['handle']})
                continue
            prod = [i for i in jsonld(pg) if 'Product' in types([i])]
            d = prod[0] if prod else {}
            offers = d.get('offers')
            offers = offers[0] if isinstance(offers, list) and offers else offers
            rows.append(photo_row | {
                'product_data': bool(prod),
                'price': bool(isinstance(offers, dict) and offers.get('price') is not None),
                'availability': bool(isinstance(offers, dict) and offers.get('availability')),
                'brand': bool(d.get('brand')),
                'identifier': any(d.get(k) or (isinstance(offers, dict) and offers.get(k)) for k in ('gtin', 'gtin8', 'gtin12', 'gtin13', 'gtin14', 'mpn', 'sku')),
                'rating': bool(d.get('aggregateRating')),
                'meta_description': bool(meta(pg, 'description')),
            })
        page = 1
        while batch and len(batch) == 250 and len(products) < max_products:
            page += 1
            st, batch = read_page(page)
            products += batch or []
        products = products[:max_products]
        if not products:
            checks.append(check('catalog', 'products', 'unmeasured', 'The public product feed (/products.json) is closed or empty', st))
        else:
            n = len(products)
            one_photo = [p for p in products if len(p.get('images') or []) <= 1]
            short = [p for p in products if words(p.get('body_html')) < 30]
            empty = [p for p in products if words(p.get('body_html')) == 0]
            titles = {}
            for p in products:
                titles.setdefault(str(p.get('title') or '').strip().lower(), []).append(p['handle'])
            dupes = {t: h for t, h in titles.items() if len(h) > 1}
            no_sku = [p for p in products if all(not (v.get('sku') or '').strip() for v in p.get('variants') or [])]

            def pct(x):
                return round(100 * len(x) / n)

            def ex(items, key='handle'):
                return [i[key] for i in items[:5]]
            catalog = {'products_read': n, 'capped': n >= max_products}
            checks.append(check('photos', 'products', 'pass' if pct(one_photo) <= 10 else ('warn' if pct(one_photo) <= 30 else 'fail'),
                                'Products with only one photo (or none)', f'{len(one_photo)} of {n} ({pct(one_photo)}%)', ex(one_photo)))
            checks.append(check('descriptions', 'products', 'pass' if pct(short) <= 10 else ('warn' if pct(short) <= 30 else 'fail'),
                                'Products whose description has fewer than 30 words', f'{len(short)} of {n} ({pct(short)}%), {len(empty)} empty', ex(short),
                                ) | {'note': 'Counted on the description field of each product. Some themes show extra text from other fields, so confirm on two of the listed pages before quoting it.'})
            checks.append(check('duplicate_titles', 'search', 'pass' if not dupes else 'warn', 'Products sharing the same title',
                                len(dupes), dict(list(dupes.items())[:5])))
            checks.append(check('sku', 'products', 'pass' if pct(no_sku) <= 10 else 'warn', 'Products with no SKU on any variant',
                                f'{len(no_sku)} of {n} ({pct(no_sku)}%)', ex(no_sku)))

            ok = [r for r in rows if 'product_data' in r]
            catalog['sampled_pages'] = rows
            alts = [r for r in rows if r.get('photos')]
            if alts:
                tot = sum(r['photos'] for r in alts)
                miss = sum(r['photos_without_alt'] for r in alts)
                ap = round(100 * miss / tot)
                checks.append(check('alt_text', 'search', 'pass' if ap <= 10 else ('warn' if ap <= 40 else 'fail'),
                                    'Product photos without alternative text (read by Google Images and screen readers)',
                                    f'{miss} of {tot} photos on {len(alts)} product pages ({ap}%)',
                                    [r['handle'] for r in alts if r['photos_without_alt']][:5]))
            if ok:
                def share(k):
                    return sum(1 for r in ok if r[k]), len(ok)
                for k, label, area in (('product_data', 'Product pages with product structured data', 'ai'),
                                       ('price', 'Price readable by Google and AI assistants', 'ai'),
                                       ('availability', 'Availability readable by Google and AI assistants', 'ai'),
                                       ('brand', 'Brand declared in the product data', 'ai'),
                                       ('identifier', 'Product code declared (GTIN, MPN or SKU)', 'ai'),
                                       ('rating', 'Review stars readable by Google (aggregateRating)', 'search'),
                                       ('meta_description', 'Product pages with a meta description', 'search')):
                    a, b = share(k)
                    checks.append(check(f'pdp_{k}', area, 'pass' if a == b else ('fail' if a == 0 else 'warn'), label, f'{a} of {b} pages checked',
                                        [r['handle'] for r in ok if not r[k]][:5]))
            else:
                checks.append(check('pdp', 'ai', 'unmeasured', 'Product pages could not be read by the script (the store limits automated visits)',
                                    [r.get('status') for r in rows], [r['url'] for r in rows if r.get('url')][:3]))

        st, body, _ = f.get('/collections.json?limit=250', 'application/json')
        if st == 200:
            try:
                data = json.loads(body)
                cols = data.get('collections', []) if isinstance(data, dict) else []
            except (ValueError, RecursionError):
                cols = []
            cols = [c for c in cols if isinstance(c, dict) and isinstance(c.get('handle'), str)] if isinstance(cols, list) else []
            if cols:
                bare = [c for c in cols if words(c.get('description') or c.get('body_html')) < 20]
                pct = round(100 * len(bare) / len(cols))
                checks.append(check('collections', 'search', 'pass' if pct <= 20 else ('warn' if pct <= 60 else 'fail'),
                                    'Collections with no real description (under 20 words)', f'{len(bare)} of {len(cols)} ({pct}%)',
                                    [c['handle'] for c in bare[:5]]))

    order = {'fail': 0, 'warn': 1, 'unmeasured': 2, 'pass': 3}
    checks.sort(key=lambda c: order[c['status']])
    summary = {s: sum(1 for c in checks if c['status'] == s) for s in order}
    return {'url': f.base, 'shopify': is_shopify, 'title': title, 'summary': summary, 'checks': checks,
            'tools': found, 'catalog': catalog, 'requests': f.count, 'audited_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}


def csv_checks(path):
    """Exact checks from the product export (Shopify admin > Products > Export, CSV for Excel or plain CSV).

    The export has one row per variant and per image; rows of the same product
    share the Handle, and only the first row carries the product fields.
    """
    products = {}
    # descriptions built with page builders can exceed csv's 128 KB default; stay bounded
    csv.field_size_limit(16 * 1024 * 1024)
    with open(path, newline='', encoding='utf-8-sig') as fh:
        for row in csv.DictReader(fh):
            handle = (row.get('Handle') or '').strip()
            if not handle:
                continue
            p = products.setdefault(handle, {'images': {}, 'variants': 0, 'barcodes': 0, 'skus': 0})
            for field in ('Title', 'Body (HTML)', 'Vendor', 'SEO Title', 'SEO Description', 'Status', 'Published'):
                if row.get(field) and field not in p:
                    p[field] = row[field]
            src = (row.get('Image Src') or '').strip()
            if src:
                p['images'][src] = (row.get('Image Alt Text') or '').strip()
            if (row.get('Variant Price') or '').strip() or (row.get('Option1 Value') or '').strip():
                p['variants'] += 1
                p['barcodes'] += bool((row.get('Variant Barcode') or '').strip())
                p['skus'] += bool((row.get('Variant SKU') or '').strip())
    live = {h: p for h, p in products.items()
            if (p.get('Status') or 'active').lower() == 'active' and (p.get('Published') or 'true').lower() != 'false'}
    if not live:
        return [check('csv', 'products', 'unmeasured', 'The product export has no active products (check the file is the Shopify product export)')], {}
    n = len(live)

    def row(cid, area, title, bad, warn_at=10, fail_at=30, note=None):
        pct = round(100 * len(bad) / n)
        c = check(cid, area, 'pass' if pct <= warn_at else ('warn' if pct <= fail_at else 'fail'), title,
                  f'{len(bad)} of {n} ({pct}%)', bad[:5])
        if note:
            c['note'] = note
        return c

    imgs = [(h, alt) for h, p in live.items() for alt in p['images'].values()]
    no_alt = [h for h, alt in imgs if not alt]
    alt_pct = round(100 * len(no_alt) / len(imgs)) if imgs else 0
    seo_title = [h for h, p in live.items() if not (p.get('SEO Title') or '').strip()]
    seo_desc = [h for h, p in live.items() if not (p.get('SEO Description') or '').strip()]
    long_title = [h for h, p in live.items() if len((p.get('SEO Title') or p.get('Title') or '').strip()) > 70]
    no_gtin = [h for h, p in live.items() if p['variants'] and p['barcodes'] == 0]
    no_vendor = [h for h, p in live.items() if not (p.get('Vendor') or '').strip()]
    short = [h for h, p in live.items() if words(p.get('Body (HTML)')) < 30]
    one_photo = [h for h, p in live.items() if len(p['images']) <= 1]
    checks = [
        check('csv_alt_text', 'search', 'pass' if alt_pct <= 10 else ('warn' if alt_pct <= 40 else 'fail'),
              'Product photos without alternative text (read by Google Images and screen readers)',
              f'{len(no_alt)} of {len(imgs)} photos ({alt_pct}%)', sorted(set(no_alt))[:5]),
        row('csv_seo_title', 'search', 'Products with no SEO title of their own (Shopify falls back to the product title)', seo_title, 30, 70),
        row('csv_seo_description', 'search', 'Products with no SEO description (Google picks a snippet itself)', seo_desc, 20, 50),
        row('csv_long_title', 'search', 'Products whose title in search results runs past 70 characters', long_title, 10, 30),
        row('csv_gtin', 'ai', 'Products with no barcode (GTIN) on any variant, used by Google Shopping and AI assistants to match products', no_gtin, 10, 50),
        row('csv_vendor', 'ai', 'Products with no brand (Vendor)', no_vendor, 5, 20),
        row('csv_descriptions', 'products', 'Products whose description has fewer than 30 words', short, 10, 30,
            'Exact: counted on the full description in the export.'),
        row('csv_photos', 'products', 'Products with only one photo (or none)', one_photo, 10, 30),
    ]
    return checks, {'products_in_export': len(products), 'active_products': n}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('url')
    ap.add_argument('--max-products', type=int, default=1000)
    ap.add_argument('--samples', type=int, default=5)
    ap.add_argument('--products-csv', help='Product export from the Shopify admin (Products > Export): exact checks on every product')
    a = ap.parse_args()
    if a.samples < 1 or a.max_products < 1:
        ap.error('--samples and --max-products must be at least 1')
    result = audit(a.url, a.max_products, a.samples)
    if a.products_csv and 'checks' in result:
        try:
            extra, info = csv_checks(a.products_csv)
        except (OSError, csv.Error, UnicodeDecodeError) as e:
            extra, info = [check('csv', 'products', 'unmeasured', f'The product export could not be read: {e}')], {}
        # the export is exact: it replaces the estimates read from the public feed
        replaced = {'photos': 'csv_photos', 'descriptions': 'csv_descriptions', 'alt_text': 'csv_alt_text'}
        have = {c['id'] for c in extra}
        result['checks'] = [c for c in result['checks'] if replaced.get(c['id']) not in have] + extra
        order = {'fail': 0, 'warn': 1, 'unmeasured': 2, 'pass': 3}
        result['checks'].sort(key=lambda c: order[c['status']])
        result['summary'] = {k: sum(1 for c in result['checks'] if c['status'] == k) for k in order}
        result['export'] = info
    json.dump(result, sys.stdout, ensure_ascii=False, indent=1)
    print()


if __name__ == '__main__':
    main()
