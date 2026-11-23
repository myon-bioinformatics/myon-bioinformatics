#!/usr/bin/env python3
"""Read-only post-deployment checks for the ten newly published guide sites."""
import json
import sys
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from playwright.sync_api import sync_playwright

NAMES = ("BlueProbe", "BrowserTestKit", "Err-o-RoadRunner", "PythDRagoras",
         "QueRyu", "SakAnalytics", "search_seq_including_spaces",
         "TalkCompress", "TaxOntOy", "TopOntoLogy")
BASE = "https://myon-bioinformatics.github.io/"
WIDTHS = (390, 1200)

def main():
    output = Path("pages-smoke-results")
    output.mkdir(exist_ok=True)
    failures = []
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        request = p.request.new_context(timeout=20000)
        for name in NAMES:
            url = BASE + name + "/"
            for width in WIDTHS:
                item = {"site": name, "width": width, "url": url}
                page = browser.new_page(viewport={"width": width, "height": 850})
                try:
                    response = page.goto(url, wait_until="networkidle", timeout=30000)
                    item["status"] = response.status if response else None
                    item["title"] = page.title()
                    item["body_length"] = len(page.locator("body").inner_text().strip())
                    item["document_width"] = page.evaluate("document.documentElement.scrollWidth")
                    item["viewport_width"] = page.evaluate("document.documentElement.clientWidth")
                    item["broken_images"] = page.locator("img").evaluate_all(
                        "(xs) => xs.filter(x => !x.complete || x.naturalWidth === 0).map(x => x.src)")
                    item["favicon"] = page.locator("link[rel~='icon']").count() > 0
                    item["links"] = page.locator("a[href]").count()
                    item["code_blocks"] = page.locator("pre, code").count()
                    item["code_overflow"] = page.locator("pre").evaluate_all(
                        "(xs) => xs.filter(x => x.scrollWidth > x.clientWidth + 2 && getComputedStyle(x).overflowX === 'visible').length")
                    page.screenshot(path=str(output / f"{name}-{width}.png"), full_page=True)
                    problems = []
                    if not response or response.status >= 400: problems.append("HTTP error")
                    if item["body_length"] < 40: problems.append("missing body text")
                    if item["document_width"] > item["viewport_width"] + 2: problems.append("horizontal overflow")
                    if item["broken_images"]: problems.append("broken images")
                    if item["code_overflow"]: problems.append("uncontained code overflow")
                    if not item["favicon"]: problems.append("favicon declaration missing")
                    if width == 390:
                        hrefs = page.locator("a[href]").evaluate_all("(xs) => [...new Set(xs.map(x => x.href))]")
                        item["link_checks"] = {}
                        for href in hrefs:
                            parsed = urlsplit(href)
                            if parsed.scheme not in ("https", "http"): continue
                            # Check same-site links; external sites may block automated clients.
                            if parsed.netloc != "myon-bioinformatics.github.io": continue
                            try:
                                res = request.get(href, max_redirects=5)
                                item["link_checks"][href] = res.status
                                if res.status >= 400: problems.append("broken internal link: " + href)
                            except Exception as exc:
                                item["link_checks"][href] = str(exc)
                                problems.append("internal link unavailable: " + href)
                    item["problems"] = problems
                    if problems: failures.append(f"{name} {width}px: {', '.join(problems)}")
                except Exception as exc:
                    item["error"] = str(exc)
                    failures.append(f"{name} {width}px: {exc}")
                finally:
                    results.append(item)
                    page.close()
        request.dispose()
        browser.close()
    (output / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"checked": len(results), "failures": failures}, ensure_ascii=False, indent=2))
    return 1 if failures else 0

if __name__ == "__main__":
    sys.exit(main())
