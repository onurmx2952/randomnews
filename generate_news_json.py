from __future__ import annotations

import json
import re
import time
import urllib.parse
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
OUTPUT_FILE = ROOT / "news-data.json"
BASE_URL = "https://news.google.com/"
MAX_ITEMS_PER_SECTION = 24
REQUEST_DELAY = 0.2
TIMEOUT = 25
MAX_SECTIONS_PER_LOCALE = 10
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/135.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class LocaleConfig:
    hl: str
    gl: str
    ceid: str
    label: str


LOCALES = [
    LocaleConfig("tr", "TR", "TR:tr", "Turkey"),
    LocaleConfig("en-US", "US", "US:en", "United States"),
    LocaleConfig("en-AU", "AU", "AU:en", "Australia"),
    LocaleConfig("en-GB", "GB", "GB:en", "United Kingdom"),
    LocaleConfig("en-CA", "CA", "CA:en", "Canada"),
    LocaleConfig("en-IN", "IN", "IN:en", "India"),
    LocaleConfig("en-NZ", "NZ", "NZ:en", "New Zealand"),
    LocaleConfig("en-IE", "IE", "IE:en", "Ireland"),
    LocaleConfig("en-SG", "SG", "SG:en", "Singapore"),
    LocaleConfig("en-ZA", "ZA", "ZA:en", "South Africa"),
    LocaleConfig("es-419", "MX", "MX:es-419", "Mexico"),
    LocaleConfig("es-419", "AR", "AR:es-419", "Argentina"),
    LocaleConfig("es-419", "CO", "CO:es-419", "Colombia"),
    LocaleConfig("pt-419", "BR", "BR:pt-419", "Brazil"),
    LocaleConfig("fr", "FR", "FR:fr", "France"),
    LocaleConfig("de", "DE", "DE:de", "Germany"),
    LocaleConfig("it", "IT", "IT:it", "Italy"),
    LocaleConfig("nl", "NL", "NL:nl", "Netherlands"),
    LocaleConfig("pl", "PL", "PL:pl", "Poland"),
    LocaleConfig("ja", "JP", "JP:ja", "Japan"),
    LocaleConfig("ko", "KR", "KR:ko", "South Korea"),
]

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})
html_cache: dict[str, str] = {}
resolve_cache: dict[str, str] = {}
article_soup_cache: dict[str, BeautifulSoup | None] = {}
article_meta_cache: dict[str, dict[str, str]] = {}


def build_url(path: str, locale: LocaleConfig) -> str:
    url = urllib.parse.urljoin(BASE_URL, path)
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    query["hl"] = [locale.hl]
    query["gl"] = [locale.gl]
    query["ceid"] = [locale.ceid]
    rebuilt = parsed._replace(query=urllib.parse.urlencode(query, doseq=True))
    return urllib.parse.urlunparse(rebuilt)


def fetch_html(url: str) -> str:
    if url in html_cache:
        return html_cache[url]
    response = session.get(url, timeout=TIMEOUT)
    response.raise_for_status()
    response.encoding = response.encoding or "utf-8"
    html = response.text
    html_cache[url] = html
    time.sleep(REQUEST_DELAY)
    return html


def absolute_url(value: str | None) -> str:
    return urllib.parse.urljoin(BASE_URL, value or "")


def proxy_image_url(url: str) -> str:
    clean = (url or "").strip()
    if not clean:
        return ""
    if clean.startswith("data:"):
        return clean
    encoded = urllib.parse.quote(clean, safe="")
    return f"https://wsrv.nl/?url={encoded}&w=1200&h=675&fit=cover&output=jpg"


def text_of(node: Any, selector: str) -> str:
    item = node.select_one(selector) if node else None
    return item.get_text(" ", strip=True) if item else ""


def collect_section_links(home_soup: BeautifulSoup, locale: LocaleConfig) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    seen: set[str] = set()

    home_url = build_url("/home", locale)
    links.append(("Top stories", home_url))
    seen.add(home_url)

    for anchor in home_soup.select("a.brSCsc[href], a.aqvwYd[href]"):
        href = build_url(anchor.get("href", ""), locale)
        label = anchor.get_text(" ", strip=True) or "Section"
        if not href or href in seen or "/home" in href:
            continue
        seen.add(href)
        links.append((label, href))

    return links[:MAX_SECTIONS_PER_LOCALE]


def extract_possible_url(text: str) -> str:
    if not text:
        return ""
    match = re.search(r"https?://[^\s'\"]+", text)
    return match.group(0) if match else ""


def clean_url(url: str) -> str:
    if not url:
        return ""

    parsed = urllib.parse.urlparse(url)
    if parsed.netloc.endswith("news.google.com"):
        query = urllib.parse.parse_qs(parsed.query)
        for key in ("url", "u", "q"):
            if query.get(key):
                return query[key][0]

    if "url=" in url:
        query = urllib.parse.parse_qs(parsed.query)
        if query.get("url"):
            return query["url"][0]

    return url


KNOWN_TRACKING_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "gclid",
    "fbclid",
    "ocid",
    "cmpid",
    "gaa_at",
    "guccounter",
    "guce_referrer",
    "guce_referrer_sig",
    "output",
}


def normalize_url(url: str) -> str:
    if not url:
        return ""
    parsed = urllib.parse.urlparse(url)
    query_items = urllib.parse.parse_qsl(parsed.query, keep_blank_values=False)
    filtered = [(k, v) for k, v in query_items if k.lower() not in KNOWN_TRACKING_KEYS]
    normalized = parsed._replace(
        scheme=parsed.scheme or "https",
        netloc=parsed.netloc.lower(),
        fragment="",
        query=urllib.parse.urlencode(filtered, doseq=True),
    )
    return urllib.parse.urlunparse(normalized)


def resolve_publisher_url(google_news_url: str) -> str:
    if not google_news_url:
        return ""
    if google_news_url in resolve_cache:
        return resolve_cache[google_news_url]

    final_url = google_news_url
    try:
        response = session.get(google_news_url, timeout=TIMEOUT, allow_redirects=True)
        final_url = response.url or google_news_url
        candidate = clean_url(final_url)
        if candidate and "news.google.com" not in urllib.parse.urlparse(candidate).netloc:
            final_url = candidate
        else:
            soup = BeautifulSoup(response.text, "html.parser")
            canonical = soup.select_one("link[rel='canonical']")
            canonical_href = canonical.get("href", "").strip() if canonical else ""
            if canonical_href and "news.google.com" not in urllib.parse.urlparse(canonical_href).netloc:
                final_url = canonical_href
            else:
                meta_refresh = soup.select_one("meta[http-equiv='refresh']")
                if meta_refresh and "content" in meta_refresh.attrs:
                    maybe = extract_possible_url(meta_refresh["content"])
                    if maybe:
                        final_url = maybe
    except Exception:
        final_url = clean_url(google_news_url)

    final_url = normalize_url(final_url)
    resolve_cache[google_news_url] = final_url or google_news_url
    time.sleep(REQUEST_DELAY)
    return resolve_cache[google_news_url]


def get_article_soup(article_url: str) -> BeautifulSoup | None:
    if not article_url:
        return None
    if article_url in article_soup_cache:
        return article_soup_cache[article_url]
    try:
        response = session.get(article_url, timeout=TIMEOUT, allow_redirects=True)
        response.raise_for_status()
        response.encoding = response.encoding or "utf-8"
        soup = BeautifulSoup(response.text, "html.parser")
        article_soup_cache[article_url] = soup
        time.sleep(REQUEST_DELAY)
        return soup
    except Exception:
        article_soup_cache[article_url] = None
        return None


def first_meta_content(soup: BeautifulSoup, selectors: list[tuple[str, str]]) -> str:
    for selector, attr in selectors:
        node = soup.select_one(selector)
        if node and node.get(attr):
            return node.get(attr, "").strip()
    return ""


def parse_json_ld_images(raw_text: str) -> list[str]:
    urls: list[str] = []
    if not raw_text:
        return urls
    try:
        payload = json.loads(raw_text)
    except Exception:
        return urls

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("image"), str):
                urls.append(value["image"])
            elif isinstance(value.get("image"), list):
                for item in value["image"]:
                    if isinstance(item, str):
                        urls.append(item)
                    elif isinstance(item, dict) and isinstance(item.get("url"), str):
                        urls.append(item["url"])
            elif isinstance(value.get("image"), dict) and isinstance(value["image"].get("url"), str):
                urls.append(value["image"]["url"])
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    return urls


def score_img(tag: Any) -> int:
    width = 0
    height = 0
    try:
        width = int(float(tag.get("width") or 0))
    except Exception:
        width = 0
    try:
        height = int(float(tag.get("height") or 0))
    except Exception:
        height = 0

    src = tag.get("src", "") or tag.get("data-src", "") or tag.get("srcset", "")
    penalty = 0
    lower_src = src.lower()
    if any(token in lower_src for token in ["logo", "icon", "avatar", "sprite", "favicon"]):
        penalty -= 100000
    return (width * height) + penalty


def extract_image_from_article(article_url: str) -> str:
    soup = get_article_soup(article_url)
    if soup is None:
        return ""

    meta_image = first_meta_content(
        soup,
        [
            ("meta[property='og:image:secure_url']", "content"),
            ("meta[property='og:image']", "content"),
            ("meta[name='twitter:image']", "content"),
            ("meta[name='twitter:image:src']", "content"),
            ("link[rel='image_src']", "href"),
        ],
    )
    if meta_image:
        return urllib.parse.urljoin(article_url, meta_image)

    for node in soup.select("script[type='application/ld+json']"):
        for candidate in parse_json_ld_images(node.get_text(" ", strip=True)):
            if candidate:
                return urllib.parse.urljoin(article_url, candidate)

    containers = soup.select("article img, main img, figure img, [role='main'] img, .article img")
    if not containers:
        containers = soup.select("img")

    usable = []
    for img in containers:
        src = img.get("src") or img.get("data-src")
        if not src or src.startswith("data:"):
            continue
        usable.append((score_img(img), urllib.parse.urljoin(article_url, src)))

    usable.sort(reverse=True, key=lambda item: item[0])
    return usable[0][1] if usable else ""


def extract_pub_date(article_url: str) -> str:
    if not article_url:
        return ""
    if article_url in article_meta_cache and article_meta_cache[article_url].get("pubDate"):
        return article_meta_cache[article_url]["pubDate"]

    soup = get_article_soup(article_url)
    if soup is None:
        return ""

    candidates = [
        first_meta_content(
            soup,
            [
                ("meta[property='article:published_time']", "content"),
                ("meta[name='article:published_time']", "content"),
                ("meta[name='pubdate']", "content"),
                ("meta[name='publish-date']", "content"),
                ("meta[name='parsely-pub-date']", "content"),
                ("meta[itemprop='datePublished']", "content"),
            ],
        )
    ]

    time_node = soup.select_one("time[datetime]")
    if time_node and time_node.get("datetime"):
        candidates.append(time_node["datetime"].strip())

    for candidate in candidates:
        if not candidate:
            continue
        try:
            iso = parsedate_to_datetime(candidate).isoformat()
            article_meta_cache.setdefault(article_url, {})["pubDate"] = iso
            return iso
        except Exception:
            pass
        try:
            normalized = candidate.replace("Z", "+00:00")
            iso = parsedate_to_datetime(normalized).isoformat()
            article_meta_cache.setdefault(article_url, {})["pubDate"] = iso
            return iso
        except Exception:
            pass
        try:
            from datetime import datetime

            iso = datetime.fromisoformat(candidate.replace("Z", "+00:00")).isoformat()
            article_meta_cache.setdefault(article_url, {})["pubDate"] = iso
            return iso
        except Exception:
            continue

    return ""


def derive_source_name(source_name: str, article_url: str) -> str:
    if source_name and source_name.strip():
        return source_name.strip()
    host = urllib.parse.urlparse(article_url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host or "Google News"


def derive_favicon(favicon: str, article_url: str) -> str:
    if favicon and favicon.strip().startswith("http"):
        return favicon.strip()
    domain = urllib.parse.urlparse(article_url).netloc.lower()
    if not domain:
        return favicon.strip() if favicon else ""
    return f"https://www.google.com/s2/favicons?domain={domain}&sz=64"


def extract_story_record(title_link: Any, section_label: str, locale: LocaleConfig) -> dict[str, Any] | None:
    title = title_link.get_text(" ", strip=True)
    if not title:
        return None

    image_tag = None
    current = title_link
    while current:
        if getattr(current, "select_one", None):
            image_tag = current.select_one("img.Quavad")
            if image_tag:
                break
        current = current.parent

    google_image = ""
    if image_tag is not None:
        srcset = image_tag.get("srcset", "")
        google_image = (
            absolute_url(srcset.split(",")[-1].strip().split(" ")[0])
            if srcset
            else absolute_url(image_tag.get("src"))
        )

    source_name = ""
    favicon = ""
    current = title_link
    while current:
        if getattr(current, "select_one", None):
            if not source_name:
                source_name = text_of(current, ".vr1PYe")
            if not favicon:
                fav = current.select_one("img.qEdqNd")
                favicon = fav.get("src", "") if fav else ""
            if source_name:
                break
        current = current.parent

    google_news_url = absolute_url(title_link.get("href"))
    publisher_url = resolve_publisher_url(google_news_url)
    best_image = extract_image_from_article(publisher_url) or google_image
    if not best_image:
        return None

    time_text = text_of(title_link.parent if getattr(title_link, "parent", None) else None, ".hvbAAd")
    byline = text_of(title_link.parent if getattr(title_link, "parent", None) else None, ".bInasb")
    resolved_source_name = derive_source_name(source_name, publisher_url)
    summary = " â¢ ".join(part for part in [time_text, byline] if part) or resolved_source_name

    return {
        "title": title,
        "summary": summary,
        "image": proxy_image_url(best_image),
        "sourceUrl": publisher_url,
        "googleNewsUrl": google_news_url,
        "sourceName": resolved_source_name,
        "favicon": derive_favicon(favicon, publisher_url),
        "topic": section_label,
        "region": locale.gl,
        "hl": locale.hl,
        "gl": locale.gl,
        "ceid": locale.ceid,
        "localeLabel": locale.label,
        "pubDate": extract_pub_date(publisher_url),
    }


def scrape_section(section_label: str, section_url: str, locale: LocaleConfig) -> list[dict[str, Any]]:
    html = fetch_html(section_url)
    soup = BeautifulSoup(html, "html.parser")
    records: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()

    for title_link in soup.select("a.gPFEn"):
        story = extract_story_record(title_link, section_label, locale)
        if not story:
            continue
        if story["title"] in seen_titles or story["sourceUrl"] in seen_urls:
            continue
        seen_titles.add(story["title"])
        seen_urls.add(story["sourceUrl"])
        records.append(story)
        if len(records) >= MAX_ITEMS_PER_SECTION:
            break

    return records


def scrape_locale(locale: LocaleConfig) -> list[dict[str, Any]]:
    home_html = fetch_html(build_url("/home", locale))
    home_soup = BeautifulSoup(home_html, "html.parser")
    section_links = collect_section_links(home_soup, locale)

    locale_items: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for section_label, section_url in section_links:
        try:
            items = scrape_section(section_label, section_url, locale)
        except Exception as exc:
            print(f"  section failed ({section_label}): {exc}")
            continue
        for item in items:
            if item["sourceUrl"] in seen_urls:
                continue
            seen_urls.add(item["sourceUrl"])
            locale_items.append(item)
    return locale_items


def main() -> None:
    all_items: list[dict[str, Any]] = []

    for locale in LOCALES:
        print(f"Scraping {locale.label} ({locale.ceid})...")
        try:
            items = scrape_locale(locale)
        except Exception as exc:
            print(f"  failed: {exc}")
            continue
        print(f"  collected {len(items)} items")
        all_items.extend(items)

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in all_items:
        key = (item["ceid"], item["sourceUrl"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    payload = {
        "generatedAt": int(time.time()),
        "count": len(deduped),
        "locales": [locale.__dict__ for locale in LOCALES],
        "items": deduped,
    }

    OUTPUT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(deduped)} items to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
