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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--out", default="data/latest.json")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

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
