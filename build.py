#!/usr/bin/env python3
"""Builds index.html for The Daily Pixel Press from the feeds in feeds.json.
Uses only Python's standard library, so there is nothing to install."""
import html
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

UA = {"User-Agent": "Mozilla/5.0 (DailyPixelPress personal news page)"}
EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def get(url, timeout=15):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def esc(s):
    return html.escape(s or "", quote=True)


def clean(text, n=230):
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "…"


def parse_date(s):
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
    except Exception:
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def local(el):
    return el.tag.split("}")[-1]


def child(el, name):
    for c in el:
        if local(c) == name:
            return c
    return None


def text(el, name):
    c = child(el, name)
    return (c.text or "").strip() if c is not None else ""


def find_image(el, raw):
    for c in el.iter():
        name = local(c)
        url = c.get("url") or ""
        if name == "thumbnail" and url:
            return url
        if name in ("content", "enclosure") and url:
            kind = (c.get("type") or "") + (c.get("medium") or "")
            if "image" in kind or re.search(r"\.(jpe?g|png|webp|gif)(\?|$)", url, re.I):
                return url
    m = re.search(r'<img[^>]+src=["\']([^"\']+)', raw or "")
    return m.group(1) if m else None


def og_image(url):
    try:
        page = get(url, 10)[:250000].decode("utf-8", "ignore")
    except Exception:
        return None
    m = (re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', page)
         or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image', page))
    return html.unescape(m.group(1)) if m else None


def fetch_feed(url):
    root = ET.fromstring(get(url))
    feed_title = ""
    for el in root.iter():
        if local(el) == "title":
            feed_title = (el.text or "").strip()
            break
    items = []
    for el in root.iter():
        if local(el) not in ("item", "entry"):
            continue
        title = clean(text(el, "title"), 200)
        link_el = child(el, "link")
        link = ((link_el.get("href") or link_el.text or "").strip()) if link_el is not None else ""
        raw = text(el, "description") or text(el, "summary") or text(el, "encoded") or text(el, "content")
        source = clean(text(el, "source"), 60) or feed_title
        if source and title.endswith(" - " + source):
            title = title[: -len(" - " + source)]
        summary = clean(raw)
        if summary.lower().startswith(title[:30].lower()):
            summary = ""  # Google News repeats the headline; skip it
        if title and link:
            items.append({
                "title": title, "link": link, "summary": summary, "source": source,
                "date": parse_date(text(el, "pubDate") or text(el, "published") or text(el, "updated") or text(el, "date")),
                "image": find_image(el, raw),
            })
    return items


def story_card(s, sec, big=False):
    when = s["date"].astimezone(sec["_tz"]).strftime("%b %-d") if s["date"] else ""
    meta = " · ".join(x for x in (s["source"], when) if x)
    art = ""
    if big:
        if s["image"]:
            art = f'<img src="{esc(s["image"])}" alt="" loading="lazy" referrerpolicy="no-referrer">'
        else:
            art = f'<div class="ph" style="background:{sec["color"]}">{sec.get("emoji", "📰")}</div>'
        art = f'<div class="pic">{art}</div>'
    summ = f'<p>{esc(s["summary"])}</p>' if s["summary"] else ""
    return (f'<div class="story{" big" if big else ""}">{art}'
            f'<h4><a href="{esc(s["link"])}" target="_blank" rel="noopener">{esc(s["title"])}</a></h4>'
            f'<div class="meta">{esc(meta)}</div>{summ}</div>')


def main():
    cfg = json.load(open("feeds.json", encoding="utf-8"))
    tz = ZoneInfo(cfg.get("timezone", "America/Chicago"))
    now = datetime.now(tz)
    cutoff = now - timedelta(days=cfg.get("max_age_days", 14))
    sections = []
    for sec in cfg["sections"]:
        items, seen = [], set()
        for url in sec["feeds"]:
            try:
                got = fetch_feed(url)
            except Exception as e:
                print("skipped feed:", url, "-", e, file=sys.stderr)
                continue
            for it in got:
                key = re.sub(r"\W+", "", it["title"].lower())[:60]
                if key not in seen:
                    seen.add(key)
                    items.append(it)
        fresh = [i for i in items if i["date"] and i["date"] >= cutoff]
        pool = sorted(fresh or items, key=lambda i: i["date"] or EPOCH, reverse=True)
        sec["stories"] = pool[: sec.get("count", 3)]
        sec["_tz"] = tz
        for s in sec["stories"][:1]:
            if not s["image"] and "news.google.com" not in s["link"]:
                s["image"] = og_image(s["link"])
        sections.append(sec)

    lead, rest = sections[0], sections[1:]
    # lead story + side list
    if lead["stories"]:
        lead_html = story_card(lead["stories"][0], lead, big=True)
        more = "".join(story_card(s, lead) for s in lead["stories"][1:])
    else:
        lead_html = '<p>No stories found today. Check back tomorrow!</p>'
        more = ""

    cd = cfg.get("countdown")
    cd_html = ""
    if cd:
        left = (date.fromisoformat(cd["date"]) - now.date()).days
        cd_html = (f'<h3>Countdown</h3><p class="count" id="cd" data-date="{esc(cd["date"])}">{left if left > 0 else "NOW"}</p>'
                   f'<p class="cl"><strong id="cl">{"days" if left > 0 else "Out now"}</strong> {esc(cd["label"]) if left > 0 else ""}</p>')

    grid = ""
    for sec in rest:
        if not sec["stories"]:
            body = '<p class="meta">Nothing new on this beat today.</p>'
        else:
            body = story_card(sec["stories"][0], sec, big=True) + "".join(story_card(s, sec) for s in sec["stories"][1:])
        grid += (f'<article><span class="chip" style="background:{sec["color"]}">{esc(sec["name"]).upper()}</span>{body}</article>')

    ticks = [f'<b>{esc(s["name"]).upper()}</b>: {esc(s["stories"][0]["title"][:70])}' for s in sections if s["stories"]][:3]
    ticker = " ★ ".join(ticks) or "THE PRESSES ARE WARMING UP"

    page = (TEMPLATE.replace("%%TITLE%%", esc(cfg.get("title", "The Daily Pixel Press")))
            .replace("%%TAGLINE%%", esc(cfg.get("tagline", "")))
            .replace("%%DATE%%", now.strftime("%A, %B %-d, %Y"))
            .replace("%%TICKER%%", ticker)
            .replace("%%LEADNAME%%", esc(lead["name"]).upper())
            .replace("%%LEADCOLOR%%", lead["color"])
            .replace("%%LEAD%%", lead_html)
            .replace("%%MORE%%", more)
            .replace("%%COUNTDOWN%%", cd_html)
            .replace("%%GRID%%", grid)
            .replace("%%UPDATED%%", now.strftime("%B %-d, %Y at %-I:%M %p %Z")))
    open("index.html", "w", encoding="utf-8").write(page)
    print("Wrote index.html with", sum(len(s["stories"]) for s in sections), "stories")


TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%%TITLE%%</title>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700;900&family=Lora:wght@400;600&family=Bangers&display=swap" rel="stylesheet">
<style>
:root{--bg:#f6efdc;--ink:#1d1a16;--muted:#5d554a;--card:#fffaf0;--red:#e63946;--yellow:#ffbe0b}
@media (prefers-color-scheme:dark){:root{--bg:#17150f;--ink:#f3ead6;--muted:#b9ad97;--card:#221f17}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:Lora,Georgia,serif;line-height:1.5}
a{color:inherit}
.wrap{max-width:1040px;margin:0 auto;padding:16px}
.top{display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;font-size:.8rem;border-top:4px double var(--ink);border-bottom:1px solid var(--ink);padding:6px 0;text-transform:uppercase;letter-spacing:.08em}
h1{font-family:"Playfair Display",Georgia,serif;font-weight:900;text-align:center;font-size:clamp(2.2rem,9vw,5rem);line-height:1;margin:14px 0 4px}
.tag{text-align:center;font-style:italic;color:var(--muted);margin:0 0 12px}
.ticker{background:var(--ink);color:var(--bg);font-family:Bangers,Impact,sans-serif;letter-spacing:.06em;padding:6px 10px;text-align:center;transform:rotate(-.5deg);margin:10px 0 18px}
.ticker b{color:var(--yellow)}
.chip{display:inline-block;font-family:Bangers,Impact,sans-serif;letter-spacing:.06em;color:#fff;padding:2px 10px;border-radius:3px;transform:rotate(-2deg);margin-bottom:6px}
h3,h4{font-family:"Playfair Display",Georgia,serif;line-height:1.2;margin:.3em 0}
h4{font-size:1.15rem}
.big h4{font-size:clamp(1.5rem,4vw,2.4rem)}
.meta{font-size:.78rem;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.story{margin-bottom:14px}.story p{margin:.3em 0}
.pic{border:2px solid var(--ink);background:var(--card);padding:5px;margin-bottom:8px}
.pic img{display:block;width:100%;height:auto;max-height:420px;object-fit:cover}
.ph{height:150px;display:flex;align-items:center;justify-content:center;font-size:4rem}
.hero{display:grid;grid-template-columns:1.6fr 1fr;gap:20px;border-bottom:4px double var(--ink);padding-bottom:16px}
.side{border-left:1px solid var(--ink);padding-left:18px}
.count{font-family:Bangers,Impact,sans-serif;font-size:4.2rem;line-height:1;color:var(--red);margin:0}
.cl{margin:0 0 12px}
.grid{display:grid;grid-template-columns:repeat(2,1fr);margin-top:20px}
.grid article{padding:0 18px 12px;border-right:1px solid var(--ink);border-bottom:1px solid var(--ink);margin-bottom:18px}
.grid article:nth-child(2n){border-right:0}
footer{margin-top:20px;border-top:4px double var(--ink);padding-top:10px;font-size:.78rem;color:var(--muted)}
@media(max-width:720px){.hero,.grid{grid-template-columns:1fr}.side{border-left:0;padding-left:0;border-top:1px solid var(--ink);padding-top:12px}.grid article{border-right:0;padding:0 0 8px}}
</style></head>
<body><div class="wrap">
<div class="top"><span>Fresh every midnight</span><span id="dt">%%DATE%%</span><span>Anime &amp; Games Edition</span></div>
<h1>%%TITLE%%</h1>
<p class="tag">%%TAGLINE%%</p>
<div class="ticker">TODAY: %%TICKER%%</div>
<section class="hero">
<div><span class="chip" style="background:%%LEADCOLOR%%">%%LEADNAME%%</span>%%LEAD%%</div>
<aside class="side">%%COUNTDOWN%%<h3>More headlines</h3>%%MORE%%</aside>
</section>
<section class="grid">%%GRID%%</section>
<footer>Headlines and summaries come from the news sites linked on each story; tap a headline to read the full article. Edition built %%UPDATED%%.</footer>
</div>
<script>
(function(){var c=document.getElementById("cd");if(!c)return;
var p=c.dataset.date.split("-"),t=new Date(+p[0],+p[1]-1,+p[2]),n=new Date(),
k=Math.round((t-new Date(n.getFullYear(),n.getMonth(),n.getDate()))/864e5);
if(k>0)c.textContent=k;else{c.textContent="NOW";document.getElementById("cl").textContent="Out now"}})();
</script>
</body></html>
"""

if __name__ == "__main__":
    main()
