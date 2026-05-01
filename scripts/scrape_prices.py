#!/usr/bin/env python3
"""Scrape Mercari, Yahoo Auction, Yahoo Freemarket for Mega Drive game prices."""

import json
import re
import time
import random
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode

import requests
from bs4 import BeautifulSoup

OUT = Path(__file__).parent.parent / "data" / "prices.json"

ITEMS = [
    {"id": "outrun",  "name": "アウトラン",   "query": "メガドライブ アウトラン"},
    {"id": "keio",    "name": "慶応遊撃隊",   "query": "メガドライブ 慶応遊撃隊"},
    {"id": "gaiares", "name": "ガイアレス",   "query": "メガドライブ ガイアレス"},
    {"id": "gods",    "name": "GODS",        "query": "メガドライブ GODS"},
]

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": _UA, "Accept-Language": "ja,en-US;q=0.9"})


def parse_yen(text: str) -> int | None:
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


# ── Mercari ───────────────────────────────────────────────────────────────────

def search_mercari(query: str) -> list[dict]:
    url = "https://api.mercari.jp/v2/entities:search"
    headers = {
        "X-Platform": "web",
        "Content-Type": "application/json",
        "Origin": "https://jp.mercari.com",
        "Referer": "https://jp.mercari.com/",
    }
    body = {
        "pageToken": "",
        "searchSessionId": f"sess{random.randint(10**8, 10**9)}",
        "indexRouting": "INDEX_ROUTING_UNSPECIFIED",
        "thumbnailTypes": [],
        "searchCondition": {
            "keyword": query,
            "excludeKeyword": "",
            "sort": "SORT_PRICE",
            "order": "ORDER_ASC",
            "status": ["STATUS_ON_SALE"],
            "categoryId": [],
            "brandId": [],
            "sellerId": [],
        },
        "defaultDatasets": ["DATASET_TYPE_MERCARI", "DATASET_TYPE_BEYOND"],
        "serviceFrom": "suruga",
        "withItemBrand": True,
        "withItemSize": False,
        "withItemPromotions": False,
        "withItemThumbnails": True,
        "withOfferPricePromotion": True,
    }
    try:
        r = SESSION.post(url, headers=headers, json=body, timeout=20)
        r.raise_for_status()
        results = []
        for it in (r.json().get("items") or [])[:10]:
            price = it.get("price")
            if price is None:
                continue
            results.append({
                "title": it.get("name", ""),
                "price": int(price),
                "url": f"https://jp.mercari.com/item/{it.get('id', '')}",
                "status": "出品中",
                "img": (it.get("thumbnails") or [None])[0],
            })
        return sorted(results, key=lambda x: x["price"])
    except Exception as e:
        print(f"    mercari error: {e}")
        return []


# ── Yahoo Auction ─────────────────────────────────────────────────────────────

def search_yahoo_auction(query: str) -> list[dict]:
    params = urlencode({"p": query, "auccat": "0", "slider": "0",
                        "s1": "cbids", "o1": "a", "n": "20", "mode": "2"})
    url = f"https://auctions.yahoo.co.jp/search/search?{params}"
    try:
        r = SESSION.get(url, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        items = (soup.select("li.Product")
                 or soup.select("div.Product")
                 or soup.select("[class*='Product']"))

        results = []
        for el in items[:10]:
            title_a = (el.select_one(".Product__title a")
                       or el.select_one("h3 a")
                       or el.select_one(".Product__titleLink")
                       or el.select_one("a[href*='page.auctions']"))
            if not title_a:
                continue
            title = title_a.get_text(strip=True)
            href = title_a.get("href", "")

            price_el = (el.select_one(".Product__priceValue")
                        or el.select_one(".Product__price--current em")
                        or el.select_one("[class*='priceValue']"))
            price = parse_yen(price_el.get_text(strip=True) if price_el else "")
            if price is None:
                continue

            results.append({"title": title, "price": price, "url": href,
                             "status": "入札受付中", "img": None})

        return sorted(results, key=lambda x: x["price"])
    except Exception as e:
        print(f"    yahoo_auction error: {e}")
        return []


# ── Yahoo Freemarket ──────────────────────────────────────────────────────────

def search_yahoo_freemarket(query: str) -> list[dict]:
    url = (f"https://paypayfleamarket.yahoo.co.jp/search/"
           f"?q={quote(query)}&sort=price_asc")
    try:
        r = SESSION.get(url, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Try Next.js hydration data first (most reliable)
        script = soup.find("script", {"id": "__NEXT_DATA__"})
        if script and script.string:
            items = _extract_nextjs_items(json.loads(script.string))
            if items:
                return sorted(items, key=lambda x: x["price"])

        # HTML fallback
        results = []
        for el in soup.select("article, [class*='item'], [class*='Item']")[:10]:
            a = el.find("a", href=True)
            price_el = el.find(class_=re.compile(r"price|Price", re.I))
            name_el = el.find(class_=re.compile(r"name|Name|title|Title", re.I))
            if not a or not price_el:
                continue
            price = parse_yen(price_el.get_text(strip=True))
            title = (name_el or a).get_text(strip=True)
            if price is None or not title:
                continue
            href = a["href"]
            if not href.startswith("http"):
                href = "https://paypayfleamarket.yahoo.co.jp" + href
            results.append({"title": title, "price": price, "url": href,
                             "status": "出品中", "img": None})

        return sorted(results, key=lambda x: x["price"])
    except Exception as e:
        print(f"    yahoo_freemarket error: {e}")
        return []


def _extract_nextjs_items(data: dict) -> list[dict]:
    try:
        page_props = data["props"]["pageProps"]
    except (KeyError, TypeError):
        return []

    raw = None
    for key in ("items", "searchItems", "itemList", "products", "result"):
        if key in page_props:
            candidate = page_props[key]
            if isinstance(candidate, list):
                raw = candidate
                break
            if isinstance(candidate, dict):
                for sub_key in ("items", "itemList", "products"):
                    if isinstance(candidate.get(sub_key), list):
                        raw = candidate[sub_key]
                        break
        if raw is not None:
            break

    if not isinstance(raw, list):
        return []

    out = []
    for it in raw[:10]:
        if not isinstance(it, dict):
            continue
        price = parse_yen(str(it.get("price") or it.get("sellingPrice") or ""))
        title = it.get("name") or it.get("title") or it.get("itemName") or ""
        iid = it.get("id") or it.get("itemId") or ""
        href = it.get("url") or f"https://paypayfleamarket.yahoo.co.jp/item/{iid}"
        img = it.get("thumbnail") or it.get("imageUrl") or None
        if price and title:
            out.append({"title": title, "price": price, "url": href,
                        "status": "出品中", "img": img})
    return out


# ── Main ──────────────────────────────────────────────────────────────────────

def min_price(listings: list[dict]) -> int | None:
    prices = [l["price"] for l in listings if l.get("price")]
    return min(prices) if prices else None


def main():
    results = {}
    for item in ITEMS:
        print(f"\n[{item['name']}]")

        print("  -> mercari")
        mc = search_mercari(item["query"])
        time.sleep(random.uniform(1.5, 2.5))

        print("  -> yahoo_auction")
        ya = search_yahoo_auction(item["query"])
        time.sleep(random.uniform(1.5, 2.5))

        print("  -> yahoo_freemarket")
        yf = search_yahoo_freemarket(item["query"])
        time.sleep(random.uniform(1.5, 2.5))

        print(f"     mercari:{len(mc)}件  ya:{len(ya)}件  yf:{len(yf)}件")

        results[item["id"]] = {
            "name": item["name"],
            "query": item["query"],
            "mercari":          {"listings": mc, "min_price": min_price(mc),  "count": len(mc)},
            "yahoo_auction":    {"listings": ya, "min_price": min_price(ya),  "count": len(ya)},
            "yahoo_freemarket": {"listings": yf, "min_price": min_price(yf),  "count": len(yf)},
        }

    output = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "items": results,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"\n✓ {OUT} に保存しました")


if __name__ == "__main__":
    main()
