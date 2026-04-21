from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parent
OUTPUT_FILE = ROOT / "news-data.json"
BASE_URL = "https://news.google.com/"
MAX_ITEMS_PER_SECTION = 24
REQUEST_DELAY = 0.12


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

html_cache: dict[str, str] = {}


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
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=25) as response:
        time.sleep(REQUEST_DELAY)
        html = response.read().decode("utf-8", "ignore")
    html_cache[url] = html
    return html


def absolute_url(value: str | None) -> str:
    return urllib.parse.urljoin(BASE_URL, value or "")


def proxy_image_url(url: str) -> str:
    clean = (url or "").strip()
    if not clean:
        return ""
    encoded = urllib.parse.quote(clean, safe="")
    return f"https://wsrv.nl/?url={encoded}&w=1200&h=675&fit=cover&output=jpg"


def text_of(node, selector: str) -> str:
    item = node.select_one(selector)
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

    return links[:10]


def scrape_section(section_label: str, section_url: str, locale: LocaleConfig) -> list[dict]:
    html = fetch_html(section_url)
    soup = BeautifulSoup(html, "html.parser")
    records: list[dict] = []
    seen_titles: set[str] = set()

    for title_link in soup.select("a.gPFEn"):
        title = title_link.get_text(" ", strip=True)
        if not title or title in seen_titles:
            continue

        image_tag = None
        current = title_link
        while current:
            if getattr(current, "select_one", None):
                image_tag = current.select_one("img.Quavad")
                if image_tag:
                    break
            current = current.parent

        if image_tag is None:
            continue

        srcset = image_tag.get("srcset", "")
        image = absolute_url(srcset.split(",")[-1].strip().split(" ")[0]) if srcset else absolute_url(image_tag.get("src"))
        if not image:
            continue
        image = proxy_image_url(image)

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

        source_name = source_name or "Google News"
        source_url = absolute_url(title_link.get("href"))
        time_text = text_of(title_link.parent if getattr(title_link, "parent", None) else soup, ".hvbAAd")
        byline = text_of(title_link.parent if getattr(title_link, "parent", None) else soup, ".bInasb")
        summary = " • ".join(part for part in [time_text, byline] if part) or source_name

        records.append(
            {
                "title": title,
                "summary": summary,
                "image": image,
                "sourceUrl": source_url,
                "sourceName": source_name,
                "favicon": favicon,
                "topic": section_label,
                "region": locale.gl,
                "hl": locale.hl,
                "gl": locale.gl,
                "ceid": locale.ceid,
                "localeLabel": locale.label,
            }
        )
        seen_titles.add(title)

        if len(records) >= MAX_ITEMS_PER_SECTION:
            break

    return records


def scrape_locale(locale: LocaleConfig) -> list[dict]:
    home_html = fetch_html(build_url("/home", locale))
    home_soup = BeautifulSoup(home_html, "html.parser")
    section_links = collect_section_links(home_soup, locale)

    locale_items: list[dict] = []
    seen_urls: set[str] = set()
    for section_label, section_url in section_links:
        try:
            items = scrape_section(section_label, section_url, locale)
        except Exception:
            continue
        for item in items:
            if item["sourceUrl"] in seen_urls:
                continue
            seen_urls.add(item["sourceUrl"])
            locale_items.append(item)
    return locale_items


def main() -> None:
    all_items: list[dict] = []
    for locale in LOCALES:
        print(f"Scraping {locale.label} ({locale.ceid})...")
        try:
            items = scrape_locale(locale)
        except Exception as exc:
            print(f"  failed: {exc}")
            continue
        print(f"  collected {len(items)} photo items")
        all_items.extend(items)

    deduped: list[dict] = []
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
    print(f"Wrote {len(deduped)} photo items to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
