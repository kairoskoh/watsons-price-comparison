import requests
from bs4 import BeautifulSoup
import json
import re
from urllib.parse import urljoin
import time
import csv
from collections import Counter

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager
    SELENIUM_AVAILABLE = True
except Exception:
    SELENIUM_AVAILABLE = False

# More realistic browser headers to reduce bot-detection blocks
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
}

JSON_HEADERS = {
    **HEADERS,
    'Accept': 'application/json, text/plain, */*',
    'X-Requested-With': 'XMLHttpRequest',
}

SITES = [
    ('Watsons SG', 'https://www.watsons.com.sg', 'SGD'),
    ('Watsons JB', 'https://www.watsons.com.my', 'MYR'),
]

# International brands stocked in both Watsons SG and MY — searching by brand
# captures the same SKUs across both sites
BRAND_TERMS = [
    'pantene', 'dove', 'gillette', 'neutrogena', 'blackmores',
    'cetaphil', 'oral-b', 'colgate', 'loreal', 'maybelline',
    'dettol', 'vaseline', 'nivea', 'la roche posay', 'bioderma',
    'cosrx', 'hada labo', 'olay', 'garnier', 'revlon',
    'head shoulders', 'herbal essences', 'rejoice',
    'swisse', 'redoxon', 'listerine', 'sensodyne',
    'biore', 'clean clear', 'pond', 'mandom', 'gatsby',
    'scott emulsion', 'cebion', 'himalaya', 'selsun',
    'wella', 'tresemme', 'sunsilk', 'clear shampoo',
]

GENERIC_TERMS = [
    'shampoo', 'conditioner', 'moisturizer', 'sunscreen',
    'toothpaste', 'vitamin', 'body wash', 'serum', 'toner',
    'face wash', 'lip balm', 'deodorant',
]

SEARCH_TERMS = BRAND_TERMS + GENERIC_TERMS

# Known Watsons category paths (same structure on both .sg and .my)
CATEGORY_PATHS = [
    '/c/hair-care',
    '/c/shampoo',
    '/c/conditioners',
    '/c/hair-treatments',
    '/c/skincare',
    '/c/moisturisers',
    '/c/suncare',
    '/c/vitamins-supplements',
    '/c/dental-oral-care',
    '/c/body-care',
    '/c/body-wash-shower-gel',
    '/c/men-grooming',
    '/c/shaving',
    '/c/makeup',
    '/c/lips',
    '/c/eyes',
    '/c/face',
]

MAX_PAGES = 3   # Pages per search/category (each page ~20-60 items)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# UI text patterns that appear in rendered pages but aren't products
_UI_PATTERNS = [
    re.compile(r'^\d+\+?\s*sold$', re.I),
    re.compile(r'^show\s+all$', re.I),
    re.compile(r'^see\s+(all|more)$', re.I),
    re.compile(r'^view\s+all$', re.I),
    re.compile(r'^\d+\s*reviews?$', re.I),
    re.compile(r'^out\s+of\s+stock$', re.I),
    re.compile(r'^add\s+to\s+(cart|bag|wishlist)$', re.I),
    re.compile(r'^\d+\s*stars?$', re.I),
    re.compile(r'^filter', re.I),
    re.compile(r'^sort\s+by', re.I),
    re.compile(r'^\d+\s*items?$', re.I),
    re.compile(r'^(next|prev(ious)?|back)$', re.I),
    re.compile(r'^(new\s+arrival|best\s+seller|trending|featured)s?$', re.I),
    re.compile(r'^brands?$', re.I),
    re.compile(r'^category$', re.I),
    re.compile(r'^\d+%\s+off$', re.I),
]


_PROMO_STRIP_RE = re.compile(
    r'(SGD|S\$|RM|MYR|\$)\s?\d+(?:\.\d{1,2})?\s+off\s+(SGD|S\$|RM|MYR|\$)?\s?\d+(?:\.\d{1,2})?',
    re.I
)


def is_valid_product(name, price):
    if not name or not price:
        return False
    name_clean = name.strip()
    price_str = str(price).strip()

    # Reject UI elements
    for pat in _UI_PATTERNS:
        if pat.match(name_clean):
            return False

    promo_keywords = ['FREE', 'OFF', 'BUY 1', 'PROMO', 'Apply code', 'min.', 'offer', 'discount']
    if any(k.lower() in name_clean.lower() for k in promo_keywords):
        return False

    if price_str in ['RM09', 'RM9', '$09', '$9', 'RM0', '$0']:
        return False

    name_stripped = re.sub(r'(S\$|RM|MYR|\$|SGD)', '', name_clean, flags=re.I)
    name_stripped = re.sub(r'[0-9.,\s\-–—]+', '', name_stripped).strip()
    if len(name_stripped) == 0:
        return False

    # Must be at least 5 characters and contain letters
    if len(name_clean) < 5 or not re.search(r'[a-zA-Z]', name_clean):
        return False

    # Must look like a product name: contain at least 2 alphabetic words
    alpha_words = re.findall(r'[a-zA-Z]+', name_clean)
    if len(alpha_words) < 2:
        return False

    if not re.search(r'(SGD|S\$|RM|MYR|\$)', price_str):
        return False

    try:
        price_match = re.search(r'([0-9]+(?:\.[0-9]{1,2})?)', price_str)
        if not price_match:
            return False
        price_val = float(price_match.group(1))
        if price_val < 1 or price_val > 10000:
            return False
    except Exception:
        return False

    return True


def clean_product_name(name):
    """Fix sites that concatenate brand name directly onto product name.
    e.g. 'PANTENEHairfall Control' -> 'PANTENE Hairfall Control'
    """
    if not name:
        return name
    # Insert space between run of ALL-CAPS and the next TitleCase word
    name = re.sub(r'([A-Z]{2,})([A-Z][a-z])', r'\1 \2', name)
    # Collapse whitespace
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def fetch(url, json_mode=False):
    headers = JSON_HEADERS if json_mode else HEADERS
    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        return r.text
    except Exception:
        return None


def parse_jsonld(soup):
    products = []
    for tag in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(tag.string or '')
        except Exception:
            continue
        items = [data] if isinstance(data, dict) else data
        for it in items:
            if not isinstance(it, dict):
                continue
            if it.get('@type') and 'Product' in str(it.get('@type')):
                name = clean_product_name(it.get('name') or '')
                offers = it.get('offers') or {}
                price = None
                url = it.get('url') or (offers.get('url') if isinstance(offers, dict) else None) or ''
                if isinstance(offers, dict):
                    price = offers.get('price') or (offers.get('priceSpecification') or {}).get('price')
                products.append({'name': name, 'price': price, 'url': url})
    return products


def extract_price(text):
    if not text:
        return None
    # Strip promotional "X off Y" patterns, e.g. "$38 off $196", before extracting
    # the real product price — otherwise the discount amount gets mistaken for the price.
    text = _PROMO_STRIP_RE.sub('', text)
    m = re.search(r'(SGD|S\$|RM|MYR|\$)\s?([0-9]+(?:\.[0-9]{1,2})?)', text)
    if m:
        return m.group(0)
    m2 = re.search(r'([0-9]+(?:\.[0-9]{1,2})?)', text)
    if m2:
        return m2.group(1)
    return None


def parse_product_cards(soup, base_url=None):
    products = []
    candidates = []
    for cls in ['product', 'product-card', 'product-item', 'item', 'card', 'productTile', 'product-tile']:
        try:
            candidates += soup.find_all(class_=re.compile(cls, re.I))
        except Exception:
            pass
    seen = set()
    for c in candidates:
        text = _PROMO_STRIP_RE.sub('', c.get_text(' ', strip=True))
        if not text or len(text) < 5:
            continue
        a_tag = c.find('a', href=True)
        link_href = a_tag['href'] if a_tag else ''
        has_product_link = bool(re.search(r'(/p/|/product|/products|/item|/prod/)', link_href, re.I))
        has_price = bool(extract_price(text))
        if not (has_price or has_product_link):
            continue
        # Build full URL
        product_url = ''
        if link_href:
            product_url = urljoin(base_url, link_href) if base_url else link_href
        name = None
        for tagname in ['h2', 'h3', 'h4', 'a', 'p', 'span']:
            t = c.find(tagname)
            if t and len(t.get_text(strip=True)) > 1:
                name = clean_product_name(t.get_text(strip=True))
                break
        if not name:
            name = clean_product_name(text.split('\n')[0][:200])
        price = extract_price(text)
        if name and is_valid_product(name, price) and (name, price) not in seen:
            seen.add((name, price))
            products.append({'name': name, 'price': price, 'url': product_url})
    return products


def find_products_by_links(soup, base_url=None):
    products = []
    seen = set()
    for a in soup.find_all('a', href=True):
        href = a['href']
        if not re.search(r'(/p/|/product|/products|/item|/prod/)', href, re.I):
            continue
        name = clean_product_name(a.get_text(strip=True) or a.get('title') or '')
        parent = a.find_parent()
        price = extract_price(parent.get_text(' ', strip=True)) if parent else None
        if not price:
            sib = a.find_next(string=re.compile(r'(SGD|S\$|RM|MYR|\$)'))
            if sib:
                price = extract_price(sib)
        if not name or not is_valid_product(name, price):
            continue
        product_url = urljoin(base_url, href) if base_url else href
        key = (name, price)
        if key not in seen:
            seen.add(key)
            products.append({'name': name, 'price': price, 'url': product_url})
    return products


# ---------------------------------------------------------------------------
# Watsons SAP Commerce (Hybris) REST API
# ---------------------------------------------------------------------------

def try_watsons_api(domain, currency):
    """Try Watsons' Hybris REST API — returns JSON product catalogue if accessible."""
    # Store code varies by market; try both possible names
    store_codes = ['watsonsSG', 'watsons'] if 'com.sg' in domain else ['watsonsMY', 'watsons', 'watsonsMY_en']

    # Category filter queries used by Hybris
    category_queries = [
        ':relevance',
        ':relevance:allCategories:HaircareCategory',
        ':relevance:allCategories:SkincareCategory',
        ':relevance:allCategories:HealthVitaminsCategory',
        ':relevance:allCategories:DentalCareCategory',
        ':relevance:allCategories:BodyCareCategory',
        ':relevance:allCategories:PersonalCareCategory',
        ':relevance:allCategories:MakeupCategory',
    ]

    results = []
    for code in store_codes:
        api_url = f'{domain}/api/v2/{code}/products/search'
        found_any = False
        for query in category_queries:
            for page in range(MAX_PAGES):
                try:
                    params = {
                        'query': query,
                        'currentPage': page,
                        'pageSize': 60,
                        'fields': 'FULL',
                        'lang': 'en',
                        'curr': currency,
                    }
                    r = requests.get(api_url, params=params, headers=JSON_HEADERS, timeout=15)
                    if r.status_code != 200:
                        break
                    data = r.json()
                    page_products = data.get('products', [])
                    if not page_products:
                        break
                    found_any = True
                    for p in page_products:
                        name = clean_product_name(p.get('name', ''))
                        price_data = p.get('price') or p.get('promotionalPrice') or {}
                        price = price_data.get('formattedValue', '')
                        slug = p.get('url') or p.get('code') or ''
                        product_url = urljoin(domain, slug) if slug and not slug.startswith('http') else slug
                        if name and price and is_valid_product(name, price):
                            results.append({'name': name, 'price': price, 'url': product_url})
                    total_pages = data.get('pagination', {}).get('totalPages', 1)
                    if page >= total_pages - 1:
                        break
                except Exception:
                    break
        if found_any:
            print(f'  API ({code}): {len(results)} products')
            return results

    return []


# ---------------------------------------------------------------------------
# Multi-strategy scraping
# ---------------------------------------------------------------------------

def scrape_search_terms(domain):
    """Search for each term, collecting results across ALL terms (no early break)."""
    results = []
    seen_keys = set()

    for term in SEARCH_TERMS:
        tries = [
            f'{domain}/search?q={term}',
            f'{domain}/search?query={term}',
            f'{domain}/catalogsearch/result/?q={term}',
        ]
        for base_url in tries:
            for page in range(MAX_PAGES):
                paged = f'{base_url}&page={page}' if page > 0 else base_url
                html = fetch(paged)
                if not html:
                    break
                soup = BeautifulSoup(html, 'lxml')
                found = parse_jsonld(soup) + parse_product_cards(soup, base_url=domain) + find_products_by_links(soup, base_url=domain)
                if not found:
                    break
                for p in found:
                    key = (p.get('name'), p.get('price'))
                    if key not in seen_keys:
                        seen_keys.add(key)
                        results.append(p)
            if results:
                break  # Found results for this term via one URL pattern; move to next term

    return results


def scrape_category_pages(domain):
    """Crawl Watsons category pages with pagination."""
    results = []
    seen_keys = set()

    # First, collect all category links from the homepage
    homepage = fetch(domain)
    if homepage:
        home_soup = BeautifulSoup(homepage, 'lxml')
        for a in home_soup.find_all('a', href=True):
            href = a['href']
            if re.search(r'(/c/|/category/|/product-categories/)', href, re.I):
                cat_path = href if href.startswith('/') else '/' + href.split(domain)[-1]
                if cat_path not in CATEGORY_PATHS and not href.startswith('http'):
                    CATEGORY_PATHS.append(cat_path)

    for path in CATEGORY_PATHS:
        for page in range(MAX_PAGES):
            page_suffix = f'?currentPage={page}' if page > 0 else ''
            cat_url = f'{domain}{path}{page_suffix}'
            html = fetch(cat_url)
            if not html:
                break
            soup = BeautifulSoup(html, 'lxml')
            found = parse_jsonld(soup) + parse_product_cards(soup, base_url=domain)
            if not found:
                break
            for p in found:
                key = (p.get('name'), p.get('price'))
                if key not in seen_keys:
                    seen_keys.add(key)
                    results.append(p)

    return results


def scrape_with_selenium(domain, search_terms):
    """Selenium fallback for JS-rendered pages. Tries category pages + search terms."""
    if not SELENIUM_AVAILABLE:
        return []

    try:
        options = Options()
        options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_argument('--window-size=1920,1080')
        options.add_experimental_option('excludeSwitches', ['enable-automation'])
        options.add_experimental_option('useAutomationExtension', False)
        options.add_argument(f'user-agent={HEADERS["User-Agent"]}')
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)

        # Suppress navigator.webdriver flag
        driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
            'source': 'Object.defineProperty(navigator, "webdriver", {get: () => undefined})'
        })

        urls_to_try = (
            [f'{domain}{p}' for p in CATEGORY_PATHS[:6]] +
            [f'{domain}/search?q={t}' for t in search_terms]
        )

        all_items = []
        seen = set()

        for url in urls_to_try:
            try:
                print(f'  Selenium: {url}')
                driver.get(url)
                time.sleep(4)

                # Scroll to trigger lazy-loading
                last_h = driver.execute_script('return document.body.scrollHeight')
                for _ in range(4):
                    driver.execute_script('window.scrollTo(0, document.body.scrollHeight);')
                    time.sleep(2)
                    new_h = driver.execute_script('return document.body.scrollHeight')
                    if new_h == last_h:
                        break
                    last_h = new_h

                driver.execute_script('window.scrollTo(0, 0);')
                time.sleep(1)

                # Parse the fully rendered HTML with BeautifulSoup
                soup = BeautifulSoup(driver.page_source, 'lxml')
                found = parse_jsonld(soup) + parse_product_cards(soup, base_url=domain)
                for p in found:
                    key = (p.get('name'), p.get('price'))
                    if key not in seen:
                        seen.add(key)
                        all_items.append(p)

                # Also try XPath anchors for product links
                try:
                    anchors = driver.find_elements(By.XPATH, "//a[contains(@href,'/p/') or contains(@href,'/product')]")
                    for e in anchors[:500]:
                        try:
                            name = clean_product_name(e.text.strip())
                            href = e.get_attribute('href') or ''
                            parent_text = ''
                            try:
                                parent_text = e.find_element(By.XPATH, '..').text
                            except Exception:
                                parent_text = e.text
                            price = extract_price(parent_text)
                            if name and is_valid_product(name, price):
                                key = (name, price)
                                if key not in seen:
                                    seen.add(key)
                                    all_items.append({'name': name, 'price': price, 'url': href})
                        except Exception:
                            pass
                except Exception:
                    pass

            except Exception as exc:
                print(f'  Selenium error for {url}: {exc}')
                continue

        driver.quit()
        return all_items

    except Exception as exc:
        print(f'Selenium init failed: {exc}')
        return []


# ---------------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------------

def is_price_only_name(name):
    if not name:
        return False
    cleaned = re.sub(r'(S\$|RM|\$|USD|SGD|MYR)', '', name, flags=re.I)
    cleaned = re.sub(r'[0-9.,\s\-–—]+', '', cleaned).strip()
    return len(cleaned) == 0


def normalize_for_matching(name):
    """Strip size/quantity units and parenthetical text before fuzzy comparison.

    Removes '680ml', '30s', '100g' etc. so that 'PANTENE Shampoo 680ml'
    and 'PANTENE Shampoo 680ML' normalise to the same string.
    Also strips parenthetical descriptions that often differ between markets.
    """
    # Remove parenthetical content
    name = re.sub(r'\([^)]*\)', '', name)
    # Remove size / quantity tokens
    name = re.sub(
        r'\b\d+\s*(?:ml|l|g|kg|mg|oz|s\b|pcs?|pc\b|pack|tab(?:lets?)?|cap(?:sules?)?|sachets?|softgels?|lozenges?|strips?)\b',
        '', name, flags=re.I
    )
    # Remove standalone numbers at end
    name = re.sub(r'\s+\d+\s*$', '', name)
    # Lowercase and keep only alphanumeric
    name = re.sub(r'[^a-z0-9 ]', '', name.lower())
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def dice_coefficient(a, b):
    if a == b:
        return 1.0
    if len(a) < 2 or len(b) < 2:
        return 0.0
    bigrams = {}
    for i in range(len(a) - 1):
        bg = a[i:i+2]
        bigrams[bg] = bigrams.get(bg, 0) + 1
    intersect = 0
    for i in range(len(b) - 1):
        bg = b[i:i+2]
        if bigrams.get(bg, 0) > 0:
            intersect += 1
            bigrams[bg] -= 1
    return (2 * intersect) / (len(a) + len(b) - 2)


def extract_price_value(price_str):
    if not price_str:
        return 0.0
    m = re.search(r'([0-9]+(?:\.[0-9]{1,2})?)', str(price_str))
    return float(m.group(1)) if m else 0.0


def prices_are_plausible(sg_price_str, jb_price_str, exchange_rate=3.1):
    """Return False if the JB/SG price ratio is implausibly low (likely a UI price, not real).

    JB price in SGD should be at least 20% and at most 250% of the SG price.
    A ratio below 0.20 suggests RM5–6 price-filter chips got scraped as prices.
    """
    sg_val = extract_price_value(sg_price_str)
    jb_val = extract_price_value(jb_price_str)
    if sg_val <= 0 or jb_val <= 0:
        return False
    jb_sgd = jb_val / exchange_rate
    ratio = jb_sgd / sg_val
    return 0.20 <= ratio <= 2.50


def generate_products_csv():
    # Two-pass matching:
    #   Pass 1 — exact name match (after cleaning)
    #   Pass 2 — fuzzy match on size-stripped names with brand-prefix sanity check
    FUZZY_THRESHOLD = 0.75

    # Store (price, url) per name per site
    sg_products = {}   # name -> {'price': ..., 'url': ...}
    jb_products = {}

    with open('rawdata.csv', newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = clean_product_name(row['name'])
            price = row['price']
            url = row.get('url', '')
            site = row['site']
            if is_price_only_name(name):
                continue
            if site == 'Watsons SG':
                if name not in sg_products:
                    sg_products[name] = {'price': price, 'url': url}
            elif site == 'Watsons JB':
                if name not in jb_products:
                    jb_products[name] = {'price': price, 'url': url}

    matched = []
    sg_unmatched = {}
    jb_unmatched = dict(jb_products)

    # Pass 1: exact match
    for sg_name, sg_data in sg_products.items():
        if sg_name in jb_products:
            jb_data = jb_products[sg_name]
            if prices_are_plausible(sg_data['price'], jb_data['price']):
                matched.append({
                    'name': sg_name,
                    'sg_price': sg_data['price'],
                    'jb_price': jb_data['price'],
                    'sg_url': sg_data['url'],
                    'jb_url': jb_data['url'],
                })
            jb_unmatched.pop(sg_name, None)
        else:
            sg_unmatched[sg_name] = sg_data

    # Pass 2: fuzzy match on normalized (size-stripped) names
    jb_items = [(name, normalize_for_matching(name)) for name in jb_unmatched]
    merged_jb = set()

    for sg_name, sg_data in sg_unmatched.items():
        sg_norm = normalize_for_matching(sg_name)
        if len(sg_norm) < 5:
            continue
        sg_words = sg_norm.split()
        best_score, best_jb_name = 0.0, None

        for jb_name, jb_norm in jb_items:
            if jb_name in merged_jb or len(jb_norm) < 5:
                continue
            jb_words = jb_norm.split()
            # Require the first "word" (brand token) to appear in the other name
            if sg_words and jb_words:
                if sg_words[0] not in jb_norm and jb_words[0] not in sg_norm:
                    continue
            score = dice_coefficient(sg_norm, jb_norm)
            if score > best_score:
                best_score = score
                best_jb_name = jb_name

        if best_score >= FUZZY_THRESHOLD and best_jb_name:
            jb_data = jb_unmatched[best_jb_name]
            if prices_are_plausible(sg_data['price'], jb_data['price']):
                matched.append({
                    'name': sg_name,
                    'sg_price': sg_data['price'],
                    'jb_price': jb_data['price'],
                    'sg_url': sg_data['url'],
                    'jb_url': jb_data['url'],
                })
            merged_jb.add(best_jb_name)

    with open('products.csv', 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['name', 'sg_price', 'jb_price', 'sg_url', 'jb_url'])
        writer.writeheader()
        writer.writerows(matched)

    print(f'\n[ok] products.csv: {len(matched)} matched products (found in both stores)')
    for m in matched[:15]:
        print(f"  {m['name'][:55]} | SG: {m['sg_price']} | JB: {m['jb_price']}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    all_products = []

    for site_name, domain, currency in SITES:
        print(f'\n{"="*70}')
        print(f'Scraping {site_name}: {domain}')
        print('='*70)
        items_seen = set()
        items = []

        def add_items(new_items, source_label):
            added = 0
            for p in new_items:
                key = (p.get('name'), p.get('price'))
                if key not in items_seen and p.get('name') and p.get('price'):
                    items_seen.add(key)
                    items.append(p)
                    added += 1
            print(f'  [{source_label}] +{added} new  (total so far: {len(items)})')

        # Strategy 1: Watsons Hybris REST API
        print('  Strategy 1: Watsons API...')
        api_items = try_watsons_api(domain, currency)
        add_items(api_items, 'API')

        # Strategy 2: Search each term (brand + generic)
        print(f'  Strategy 2: Searching {len(SEARCH_TERMS)} terms...')
        search_items = scrape_search_terms(domain)
        add_items(search_items, 'Search')

        # Strategy 3: Category pages
        print('  Strategy 3: Category pages...')
        cat_items = scrape_category_pages(domain)
        add_items(cat_items, 'Category')

        # Strategy 4: Selenium if still below threshold
        if SELENIUM_AVAILABLE and len(items) < 100:
            print(f'  Strategy 4: Selenium (only {len(items)} products so far)...')
            sel_items = scrape_with_selenium(domain, BRAND_TERMS)
            add_items(sel_items, 'Selenium')

        print(f'\n[done] {site_name}: {len(items)} unique products')
        all_products.extend([{'site': site_name, **i} for i in items])

    print('\n' + '='*70)
    print('SUMMARY')
    print('='*70)
    print(f'Total raw products: {len(all_products)}')
    site_counts = Counter(p['site'] for p in all_products)
    for site, count in site_counts.items():
        print(f'  {site}: {count}')

    if all_products:
        cleaned = [
            {
                'site': p.get('site', ''),
                'name': p.get('name', ''),
                'price': p.get('price', ''),
                'url': p.get('url', ''),
            }
            for p in all_products
        ]
        with open('rawdata.csv', 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=['site', 'name', 'price', 'url'])
            writer.writeheader()
            writer.writerows(cleaned)
        print(f'\n[ok] rawdata.csv: {len(cleaned)} rows')
        generate_products_csv()


if __name__ == '__main__':
    main()
