import asyncio
import json
import logging
import os
import random
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree
from playwright.async_api import async_playwright, Browser, BrowserContext, Error as PlaywrightError, Page
from playwright_stealth.stealth import Stealth
from urllib.parse import urlparse, urlunparse


# ------------------------------
# Logging setup
# ------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("mouser_parser")


# ------------------------------
# Data classes and helpers
# ------------------------------
@dataclass
class Config:
    output_file: str
    proxies_file: str
    threads: int
    timeout: int
    retries: int


def load_config(config_path: str) -> Config:
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        config = Config(
            output_file=raw.get("output_file", "mouser_products.json"),
            proxies_file=raw.get("proxies_file", "proxies.txt"),
            threads=int(raw.get("threads", 4)),
            timeout=int(raw.get("timeout", 10)),
            retries=int(raw.get("retries", 3)),
        )
        # Validation
        ok = True
        if config.threads < 1:
            logger.warning("Config validation: threads < 1; corrected to 1")
            config.threads = 1
        if config.timeout < 1:
            logger.warning("Config validation: timeout < 1; corrected to 10")
            config.timeout = 10
        if config.retries < 0:
            logger.warning("Config validation: retries < 0; corrected to 0")
            config.retries = 0
        logger.info("Config load: успешно")
        return config
    except Exception as e:
        logger.error(f"Config load error: {e}")
        # Fallback defaults
        fallback = Config(
            output_file="mouser_products.json",
            proxies_file="proxies.txt",
            threads=4,
            timeout=10,
            retries=3,
        )
        logger.info("Config load: требуется коррекция (использую значения по умолчанию)")
        return fallback


def parse_proxy_line(line: str) -> Optional[Dict[str, str]]:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    # Expected format: user:pass@ip:port
    try:
        creds, hostport = line.split("@", 1)
        user, pwd = creds.split(":", 1)
        host, port = hostport.split(":", 1)
        server = f"http://{host}:{port}"
        return {"server": server, "username": user, "password": pwd}
    except Exception:
        return None


def load_proxies(proxies_path: str) -> List[Dict[str, str]]:
    proxies: List[Dict[str, str]] = []
    try:
        with open(proxies_path, "r", encoding="utf-8") as f:
            for raw in f:
                proxy = parse_proxy_line(raw)
                if proxy is None:
                    if raw.strip() and not raw.strip().startswith("#"):
                        logger.error(f"Proxy parse error (skip): {raw.strip()}")
                    continue
                proxies.append(proxy)
        if proxies:
            logger.info(f"Proxies load: успешно ({len(proxies)})")
        else:
            logger.info("Proxies load: требуется коррекция (файл пуст или отсутствуют валидные записи)")
        return proxies
    except FileNotFoundError:
        logger.error(f"Proxies file not found: {proxies_path}")
        logger.info("Proxies load: требуется коррекция (продолжаю без прокси)")
        return []
    except Exception as e:
        logger.error(f"Proxies load error: {e}")
        logger.info("Proxies load: требуется коррекция (продолжаю без прокси)")
        return []


def discover_xml_files(urls_dir: str) -> List[str]:
    files: List[str] = []
    try:
        p = Path(urls_dir)
        if not p.exists() or not p.is_dir():
            logger.error(f"URLS directory not found or not a directory: {urls_dir}")
            logger.info("XML discovery: требуется коррекция")
            return []
        for entry in p.iterdir():
            if entry.is_file() and entry.suffix.lower() == ".xml":
                files.append(str(entry))
        if files:
            logger.info(f"XML discovery: успешно ({len(files)} файлов)")
        else:
            logger.info("XML discovery: требуется коррекция (XML не найдены)")
        return files
    except Exception as e:
        logger.error(f"XML discovery error: {e}")
        logger.info("XML discovery: требуется коррекция")
        return []


def parse_urls_from_xml(xml_path: str) -> List[str]:
    urls: List[str] = []
    try:
        with open(xml_path, "rb") as f:
            tree = etree.parse(f)
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        for loc in tree.xpath("//sm:url/sm:loc", namespaces=ns):
            if loc.text:
                urls.append(loc.text.strip())
        if urls:
            logger.info(f"XML parse: успешно ({os.path.basename(xml_path)}: {len(urls)} URL)")
        else:
            logger.info(f"XML parse: требуется коррекция ({os.path.basename(xml_path)}: URL не найдены)")
        return urls
    except Exception as e:
        logger.error(f"XML parse error in {xml_path}: {e}")
        logger.info("XML parse: требуется коррекция (продолжаю)")
        return []


# ------------------------------
# Playwright helpers
# ------------------------------
USER_AGENTS = [
    # A few realistic desktop Chrome UAs
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]


def random_viewport() -> Tuple[int, int]:
    widths = [1366, 1440, 1536, 1600, 1920]
    heights = [768, 900, 864, 900, 1080]
    i = random.randrange(len(widths))
    # Add slight jitter
    return widths[i] + random.randint(-16, 16), heights[i] + random.randint(-16, 16)


async def simulate_human_interaction(page: Page) -> None:
    try:
        width, height = page.viewport_size.get("width", 1366), page.viewport_size.get("height", 768)
        # Random small mouse moves
        for _ in range(random.randint(2, 5)):
            await page.mouse.move(random.randint(0, width), random.randint(0, height), steps=random.randint(10, 30))
        # Random scrolls
        for _ in range(random.randint(1, 3)):
            delta = random.randint(200, 800)
            await page.mouse.wheel(0, delta)
            await page.wait_for_timeout(random.randint(200, 500))
        # Try dismiss common consent banners
        for sel in [
            "button#onetrust-accept-btn-handler",
            "button#onetrust-reject-all-handler",
            "button[aria-label='Accept All Cookies']",
            "button:has-text('Accept All')",
            "button:has-text('Accept Cookies')",
        ]:
            try:
                if await page.locator(sel).count() > 0:
                    await page.locator(sel).first().click(timeout=1000)
                    await page.wait_for_timeout(200)
            except Exception:
                pass
    except Exception:
        pass


async def new_context_with_stealth(browser: Browser) -> BrowserContext:
    viewport = random_viewport()
    context = await browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport={"width": viewport[0], "height": viewport[1]},
        locale="en-US",
        timezone_id=random.choice(["Europe/Berlin", "UTC", "America/New_York"]),
        java_script_enabled=True,
        color_scheme=random.choice(["light", "dark"]),
        device_scale_factor=random.choice([1, 1.25, 1.5]),
        permissions=[],
        bypass_csp=True,
    )
    # Apply stealth evasions on context
    try:
        stealth = Stealth()
        await stealth.apply_stealth_async(context)
    except Exception:
        pass
    # Extra headers
    await context.set_extra_http_headers(
        {
            "Accept-Language": "en-US,en;q=0.9",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
        }
    )
    return context


async def is_blocked(page: Page) -> bool:
    try:
        content = (await page.content()).lower()
        indicators = [
            "access denied",
            "unusual traffic",
            "are you a human",
            "akamai",
            "captcha",
            "request unsuccessful",
            "forbidden",
            "bot detected",
        ]
        return any(ind in content for ind in indicators)
    except Exception:
        return False


def normalize_to_eu_domain(url: str) -> str:
    try:
        parts = urlparse(url)
        host = parts.netloc
        if host.endswith("mouser.com") and not host.startswith("eu."):
            new_host = "eu.mouser.com"
            return urlunparse((parts.scheme or "https", new_host, parts.path, parts.params, parts.query, parts.fragment))
    except Exception:
        pass
    return url


# ------------------------------
# Extraction helpers
# ------------------------------
async def get_text(page: Page, selector: str) -> Optional[str]:
    try:
        loc = page.locator(selector)
        if await loc.count() == 0:
            return None
        txt = await loc.first().inner_text()
        if txt is None:
            return None
        cleaned = re.sub(r"\s+", " ", txt).strip()
        return cleaned or None
    except Exception:
        return None


async def get_attr(page: Page, selector: str, attr: str) -> Optional[str]:
    try:
        loc = page.locator(selector)
        if await loc.count() == 0:
            return None
        val = await loc.first().get_attribute(attr)
        if val is None:
            return None
        return val.strip() or None
    except Exception:
        return None


def parse_first_int(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    m = re.search(r"(\d[\d,\s]*)", text)
    if not m:
        return None
    try:
        digits = re.sub(r"[^0-9]", "", m.group(1))
        return int(digits) if digits else None
    except Exception:
        return None


def parse_first_float(text: Optional[str]) -> Optional[float]:
    if not text:
        return None
    m = re.search(r"([0-9]+[\.,]?[0-9]*)", text)
    if not m:
        return None
    num = m.group(1).replace(",", ".")
    try:
        return float(num)
    except Exception:
        return None


async def extract_stock(page: Page) -> Tuple[Optional[int], Optional[str]]:
    # Attempt 1: Using known ids
    try:
        label = page.locator("#stockLabelHeader")
        if await label.count() > 0:
            container = await label.first().element_handle()
            if container:
                div = await container.evaluate_handle("e => e.parentElement && e.parentElement.querySelector('div')")
                if div:
                    text = await div.evaluate("e => e.innerText")
                    if text:
                        number = parse_first_int(text)
                        stock_text = text.strip()
                        return number, stock_text
    except Exception:
        pass
    # Attempt 2: Look for any element containing 'Stock:' and a sibling div
    try:
        label2 = page.locator("text=Stock:")
        if await label2.count() > 0:
            elem = await label2.first().element_handle()
            if elem:
                div = await elem.evaluate_handle("e => e.parentElement && e.parentElement.querySelector('div')")
                if div:
                    text = await div.evaluate("e => e.innerText")
                    if text:
                        number = parse_first_int(text)
                        stock_text = text.strip()
                        return number, stock_text
    except Exception:
        pass
    return None, None


async def extract_factory_lead_time(page: Page) -> Optional[str]:
    try:
        label = page.locator("#factoryLeadTimeLabelHeader")
        if await label.count() > 0:
            container = await label.first().element_handle()
            if container:
                div = await container.evaluate_handle("e => e.parentElement && e.parentElement.querySelector('div')")
                if div:
                    text = await div.evaluate("e => e.innerText")
                    if text:
                        return re.sub(r"\s+", " ", text).strip()
    except Exception:
        pass
    return None


async def ensure_specs_visible(page: Page) -> None:
    try:
        btn = page.locator("#btnSpecification")
        if await btn.count() > 0:
            await btn.first().click(timeout=2000)
            await page.wait_for_timeout(500)
    except Exception:
        pass


async def extract_specifications(page: Page) -> Dict[str, str]:
    await ensure_specs_visible(page)
    specs: Dict[str, str] = {}
    # Try table rows
    try:
        rows = page.locator("table >> tr")
        count = await rows.count()
        for i in range(count):
            row = rows.nth(i)
            cells = row.locator("td, th")
            if await cells.count() >= 2:
                key = re.sub(r"\s+", " ", (await cells.nth(0).inner_text()).strip())
                val = re.sub(r"\s+", " ", (await cells.nth(1).inner_text()).strip())
                if key and val:
                    specs[key] = val
    except Exception:
        pass
    # Try definition list
    try:
        dts = page.locator("dt")
        dds = page.locator("dd")
        n = min(await dts.count(), await dds.count())
        for i in range(n):
            key = re.sub(r"\s+", " ", (await dts.nth(i).inner_text()).strip())
            val = re.sub(r"\s+", " ", (await dds.nth(i).inner_text()).strip())
            if key and val:
                specs[key] = val
    except Exception:
        pass
    return specs


async def extract_prices(page: Page) -> List[Dict[str, Any]]:
    prices: List[Dict[str, Any]] = []
    # Try to locate the pricing section by heading then parse following table
    try:
        heading = page.locator("#h2PricingTitle")
        if await heading.count() > 0:
            # Heuristic: the nearest following table
            tables = page.locator("table")
            tcount = await tables.count()
            for i in range(tcount):
                table = tables.nth(i)
                text = (await table.inner_text()).lower()
                if "qty" in text and ("price" in text or "unit" in text):
                    # Parse rows
                    rows = table.locator("tr")
                    rcount = await rows.count()
                    for r in range(1, rcount):  # skip header
                        cells = rows.nth(r).locator("td")
                        if await cells.count() >= 2:
                            qty_text = await cells.nth(0).inner_text()
                            price_text = await cells.nth(1).inner_text()
                            qty = parse_first_int(qty_text)
                            val = parse_first_float(price_text)
                            if qty is not None and val is not None:
                                prices.append({"qty": qty, "value": val})
                    if prices:
                        return prices
    except Exception:
        pass
    # Fallback: parse any row-like lines for qty and price
    try:
        text = (await page.content()).lower()
        # Not robust, but last resort
        for m in re.finditer(r"qty[^\n]*?([0-9,]+)[^\n]+?([0-9]+[\.,]?[0-9]*)", text):
            qty = parse_first_int(m.group(1))
            val = parse_first_float(m.group(2))
            if qty is not None and val is not None:
                prices.append({"qty": qty, "value": val})
    except Exception:
        pass
    return prices


async def extract_product_data(page: Page, url: str) -> Dict[str, Any]:
    sku = await get_text(page, "h1.panel-title")
    mouser_sku = await get_text(page, "#spnMouserPartNumFormattedForProdInfo")
    mpn = await get_text(page, "#spnManufacturerPartNumber")
    producer = await get_text(page, "#lnkManufacturerName")
    description = await get_text(page, "#spnDescription")
    datasheet_url = await get_attr(page, "#pdp-datasheet_0", "href")
    picture_url = await get_attr(page, "img.img-responsive.imgSlide", "src")
    stock_number, stock_text = await extract_stock(page)
    factory_lead_time = await extract_factory_lead_time(page)
    price = await extract_prices(page)
    specifications = await extract_specifications(page)

    data: Dict[str, Any] = {
        "sku": sku or None,
        "mouser_sku": mouser_sku or None,
        "mpn": mpn or None,
        "producer": producer or None,
        "description": description or None,
        "url": url,
        "datasheet_url": datasheet_url or None,
        "picture_url": picture_url or None,
        "stock_number": stock_number if stock_number is not None else None,
        "stock_text": stock_text or None,
        "factory_lead_time": factory_lead_time or None,
        "price": price or [],
        "specifications": specifications or {},
    }
    return data


# ------------------------------
# Runner
# ------------------------------
async def fetch_with_retries(browser: Browser, url: str, timeout_s: int, retries: int) -> Optional[Dict[str, Any]]:
    attempt = 0
    last_error: Optional[str] = None
    while attempt <= retries:
        attempt += 1
        context: Optional[BrowserContext] = None
        try:
            context = await new_context_with_stealth(browser)
            page = await context.new_page()
            # Warm-up to base domain to obtain cookies and reduce suspicion
            eu_url = normalize_to_eu_domain(url)
            try:
                base = urlparse(eu_url)
                warmup = f"{base.scheme}://{base.netloc}/"
                await page.goto(warmup, wait_until="domcontentloaded", timeout=timeout_s * 1000)
                await page.wait_for_timeout(800)
                await simulate_human_interaction(page)
            except Exception:
                pass
            await page.goto(eu_url, wait_until="domcontentloaded", timeout=timeout_s * 1000)
            await page.wait_for_timeout(500)
            await simulate_human_interaction(page)
            # Wait for key selectors or a reasonable timeout
            try:
                await page.wait_for_selector("#spnManufacturerPartNumber, h1.panel-title", timeout=timeout_s * 1000)
            except PlaywrightError:
                pass
            if await is_blocked(page):
                last_error = "blocked"
                raise PlaywrightError("Blocked by anti-bot")
            data = await extract_product_data(page, url)
            # Quick validation
            if any([data.get("sku"), data.get("mouser_sku"), data.get("mpn")]):
                logger.info(f"Parse {url[:80]}: успешно")
            else:
                logger.info(f"Parse {url[:80]}: требуется коррекция (поля не найдены)")
            return data
        except Exception as e:
            last_error = str(e)
            await asyncio.sleep(random.uniform(0.8, 2.2))
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass
    logger.error(f"Failed to parse after retries ({url}): {last_error}")
    return None


async def worker(name: str, browser: Browser, queue: "asyncio.Queue[str]", results: List[Dict[str, Any]], timeout_s: int, retries: int) -> None:
    while True:
        try:
            url = await queue.get()
        except asyncio.CancelledError:
            return
        try:
            data = await fetch_with_retries(browser, url, timeout_s, retries)
            if data is not None:
                results.append(data)
        finally:
            queue.task_done()


async def run_parser(config_path: str = "config") -> int:
    config = load_config(config_path)
    proxies = load_proxies(config.proxies_file)

    urls_dir = os.path.join(os.getcwd(), "URLS")
    xml_files = discover_xml_files(urls_dir)
    all_urls: List[str] = []
    for xml in xml_files:
        all_urls.extend(parse_urls_from_xml(xml))
    if not all_urls:
        logger.error("No URLs to process. Exiting.")
        return 1

    # Prepare output container
    results: List[Dict[str, Any]] = []

    # Prepare concurrency
    threads = max(1, config.threads)

    stealth_ctx = Stealth()
    async with stealth_ctx.use_async(async_playwright()) as p:
        browsers: List[Browser] = []
        try:
            if proxies:
                # Launch up to `threads` browsers with rotating proxies
                for i in range(min(threads, len(proxies))):
                    proxy = proxies[i % len(proxies)]
                    b = await p.chromium.launch(
                        headless=True,
                        proxy=proxy,
                        args=[
                            "--no-sandbox",
                            "--disable-dev-shm-usage",
                        ],
                    )
                    browsers.append(b)
            else:
                # No proxies available
                b = await p.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                    ],
                )
                browsers.append(b)

            # Create queue
            queue: "asyncio.Queue[str]" = asyncio.Queue()
            for url in all_urls:
                await queue.put(url)

            # Spawn workers
            worker_tasks: List[asyncio.Task] = []
            for i in range(threads):
                browser = browsers[i % len(browsers)]
                task = asyncio.create_task(
                    worker(
                        name=f"worker-{i+1}",
                        browser=browser,
                        queue=queue,
                        results=results,
                        timeout_s=config.timeout,
                        retries=config.retries,
                    )
                )
                worker_tasks.append(task)

            await queue.join()

            # Cancel workers
            for t in worker_tasks:
                t.cancel()
            await asyncio.gather(*worker_tasks, return_exceptions=True)
        finally:
            for b in browsers:
                try:
                    await b.close()
                except Exception:
                    pass

    # Write output JSON
    try:
        with open(config.output_file, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        # Validate write
        ok = os.path.exists(config.output_file) and os.path.getsize(config.output_file) > 0
        if ok:
            logger.info(f"Write JSON: успешно ({config.output_file}, {len(results)} объектов)")
        else:
            logger.info("Write JSON: требуется коррекция")
    except Exception as e:
        logger.error(f"Write JSON error: {e}")
        logger.info("Write JSON: требуется коррекция")
        return 1

    return 0


def main() -> None:
    exit_code = asyncio.run(run_parser("config"))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()