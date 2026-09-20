#!/usr/bin/env python3
"""
fetch_news.py - Google News RSS から候補記事を収集し JSON として出力する。

役割はここまで（収集のみ）。
スコアリング・日本語要約・HTML生成は Claude のスケジュールタスクが行うため、
このスクリプトは ANTHROPIC_API_KEY も Gmail も必要としない。

設定 (config.yaml) は GitHub Actions Secret から実行時に書き出す想定で、
リポジトリには含めない。
"""

import argparse
import hashlib
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

# macOS の Python はシステムCAを見ないことがあるため certifi を優先する
try:
    import os as _os, certifi as _certifi
    _os.environ.setdefault("SSL_CERT_FILE", _certifi.where())
except Exception:
    pass

import feedparser
import yaml
from dateutil import parser as dateutil_parser

JST = timezone(timedelta(hours=9))

RSS_ENDPOINTS = {
    "ja": "https://news.google.com/rss/search?q={query}&hl=ja&gl=JP&ceid=JP:ja",
    "en_us": "https://news.google.com/rss/search?q={query}&hl=en&gl=US&ceid=US:en",
    "en_asia": "https://news.google.com/rss/search?q={query}&hl=en&gl=SG&ceid=SG:en",
    "en_eu": "https://news.google.com/rss/search?q={query}&hl=en&gl=GB&ceid=GB:en",
}
QUERY_KEY_TO_REGION = {"ja": "japan", "en_us": "us", "en_asia": "asia_oceania", "en_eu": "eu"}
QUERY_KEY_TO_LANGUAGE = {"ja": "ja", "en_us": "en", "en_asia": "en", "en_eu": "en"}

BLOCKED_DOMAINS = {
    "starnewskorea.com", "koreaboo.com", "soompi.com", "allkpop.com", "kpopstarz.com",
    "espn.com", "bleacherreport.com", "sportingnews.com",
    "tmz.com", "buzzfeed.com", "dailymail.co.uk",
    "ign.com", "gamespot.com",
}


def strip_html(text):
    clean = re.sub(r"<[^>]+>", "", text or "")
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
        clean = clean.replace(a, b)
    return re.sub(r"\s+", " ", clean).strip()


def parse_published(entry):
    if getattr(entry, "published_parsed", None):
        try:
            import calendar
            return datetime.fromtimestamp(calendar.timegm(entry.published_parsed), tz=timezone.utc)
        except Exception:
            pass
    if getattr(entry, "published", None):
        try:
            return dateutil_parser.parse(entry.published).astimezone(timezone.utc)
        except Exception:
            pass
    return None


def extract_source(entry):
    src = getattr(entry, "source", None)
    if src is not None and getattr(src, "title", None):
        return src.title
    title = getattr(entry, "title", "") or ""
    if " - " in title:
        return title.rsplit(" - ", 1)[1].strip()
    return "Unknown"


def clean_title(title):
    return title.rsplit(" - ", 1)[0].strip() if " - " in title else (title or "").strip()


def fetch_one(job):
    topic_name, query_key, query, cutoff, max_per_query = job
    url = RSS_ENDPOINTS[query_key].format(query=quote_plus(query))
    out = []
    try:
        feed = feedparser.parse(url)
    except Exception as e:
        print(f"  ! failed [{query_key}]: {e}", file=sys.stderr)
        return out
    for entry in feed.entries[:max_per_query]:
        try:
            link = entry.get("link", "")
            if not link:
                continue
            low = link.lower()
            if any(d in low for d in BLOCKED_DOMAINS):
                continue
            published = parse_published(entry)
            if published is None or published < cutoff:
                continue
            title = clean_title(entry.get("title", ""))
            if not title:
                continue
            out.append({
                "id": hashlib.md5(link.encode("utf-8")).hexdigest()[:10],
                "title": title,
                "url": link,
                "source": extract_source(entry),
                "published_at": published.astimezone(JST).isoformat(timespec="minutes"),
                "region": QUERY_KEY_TO_REGION[query_key],
                "topic": topic_name,
                "snippet": strip_html(entry.get("summary", ""))[:300],
                "language": QUERY_KEY_TO_LANGUAGE[query_key],
            })
        except Exception as e:
            print(f"  ! entry error: {e}", file=sys.stderr)
    return out



# ---------------------------------------------------------------------------
# Google News の中継URL（news.google.com/rss/articles/CBMi...）を
# 実際の記事URLに解決する。解決できない場合は中継URLをそのまま使う。
# ---------------------------------------------------------------------------
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _resolve_google_url(gurl, timeout=20):
    import urllib.request, urllib.parse, json as _json
    if "news.google.com" not in gurl or "/articles/" not in gurl:
        return gurl
    token = gurl.split("/articles/")[1].split("?")[0]
    try:
        html = urllib.request.urlopen(
            urllib.request.Request(gurl, headers={"User-Agent": UA}), timeout=timeout
        ).read().decode("utf-8", "ignore")
        sg = re.search(r'data-n-a-sg="([^"]+)"', html)
        ts = re.search(r'data-n-a-ts="([^"]+)"', html)
        if not (sg and ts):
            return gurl
        inner = _json.dumps(["garturlreq", [["X", "X", ["X", "X"], None, None, 1, 1,
                                             "US:en", None, 1, None, None, None, None,
                                             None, 0, 1],
                                            "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0,
                                            None, 0],
                             token, int(ts.group(1)), sg.group(1)])
        payload = _json.dumps([[["Fbv4je", inner, None, "generic"]]])
        body = urllib.parse.urlencode({"f.req": payload}).encode()
        res = urllib.request.urlopen(
            urllib.request.Request(
                "https://news.google.com/_/DotsSplashUi/data/batchexecute",
                data=body,
                headers={"User-Agent": UA,
                         "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"}),
            timeout=timeout).read().decode("utf-8", "ignore")
        m = re.search(r'garturlres\\",\\"(https?://[^\\"]+)', body_text := res)
        if m:
            return m.group(1)
        m = re.search(r'(https?://(?!news\.google)[^\\"]+)', body_text)
        return m.group(1) if m else gurl
    except Exception:
        return gurl


def resolve_articles(articles, workers=8):
    """各記事の url を実記事URLに置き換え、中継URLは google_url に退避する。"""
    from concurrent.futures import ThreadPoolExecutor
    urls = [a["url"] for a in articles]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        resolved = list(pool.map(_resolve_google_url, urls))
    ok = 0
    for a, r in zip(articles, resolved):
        a["google_url"] = a["url"]
        a["url"] = r
        if "news.google.com" not in r:
            ok += 1
    print(f"Resolved {ok}/{len(articles)} article URLs to publisher links", file=sys.stderr)
    return articles

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--out", default="data/latest.json")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--no-resolve", action="store_true",
                    help="Google News の中継URLを解決しない")
    ap.add_argument("--resolve-only", metavar="JSON",
                    help="既存の収集JSONのURLだけを解決して上書きする")
    args = ap.parse_args()

    if args.resolve_only:
        with open(args.resolve_only, encoding="utf-8") as f:
            payload = json.load(f)
        resolve_articles(payload["articles"], workers=args.workers)
        with open(args.resolve_only, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        return

    config = yaml.safe_load(open(args.config, encoding="utf-8"))
    news = config["news"]
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=news.get("hours_lookback", 24))
    max_per_query = news.get("max_per_query", 10)

    jobs = []
    for topic in news["topics"]:
        for query_key, queries in (topic.get("queries") or {}).items():
            if query_key in RSS_ENDPOINTS:
                for q in queries:
                    jobs.append((topic["name"], query_key, q, cutoff, max_per_query))

    print(f"Fetching {len(jobs)} queries with {args.workers} workers ...", file=sys.stderr)
    seen, articles = set(), []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for batch in pool.map(fetch_one, jobs):
            for a in batch:
                if a["id"] not in seen:
                    seen.add(a["id"])
                    articles.append(a)

    if not args.no_resolve and articles:
        resolve_articles(articles, workers=args.workers)

    articles.sort(key=lambda a: a["published_at"], reverse=True)
    payload = {
        "meta": {
            "generated_at": datetime.now(tz=JST).isoformat(timespec="seconds"),
            "hours_lookback": news.get("hours_lookback", 24),
            "region_slots": news["region_slots"],
            "articles_per_digest": news["articles_per_digest"],
            "topic_weights": {t["name"]: t.get("weight", 1) for t in news["topics"]},
            "counts_by_region": {r: sum(1 for a in articles if a["region"] == r)
                                 for r in QUERY_KEY_TO_REGION.values()},
            "total": len(articles),
            "queries_run": len(jobs),
        },
        "articles": articles,
    }

    import os
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print(json.dumps(payload["meta"], ensure_ascii=False, indent=1), file=sys.stderr)


if __name__ == "__main__":
    main()
