import requests
from bs4 import BeautifulSoup
import json
import re
from urllib.parse import urljoin
import time
import csv
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager
    SELENIUM_AVAILABLE = True
except Exception:
    SELENIUM_AVAILABLE = False

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

SITES = [
    ('Watsons SG', 'https://www.watsons.com.sg'),
    ('Watsons JB', 'https://www.watsons.com.my'),
]

SEARCH_TERMS = ['skincare', 'shampoo', 'vitamin', 'soap', 'toothpaste']

def is_valid_product(name, price):
    """Filter out promotions and category pages"""
    if not name or not price:
        return False
    
    name_clean = name.strip()
    price_str = str(price).strip()
    
    # Filter out promotional entries
    promo_keywords = ['FREE', 'OFF', 'BUY 1', 'PROMO', 'Apply code', 'min.', 'offer', 'discount']
    if any(keyword.lower() in name_clean.lower() for keyword in promo_keywords):
        return False
    
    # Filter out category pages (prices like "RM09")
    if price_str in ['RM09', 'RM9', '$09', '$9', 'RM0', '$0']:
        return False
    
    # Filter out product names that are just prices or price ranges (e.g. "S$14.63 S$20.90")
    name_stripped = re.sub(r'(S\$|RM|MYR|\$|SGD)', '', name_clean, flags=re.I)
    name_stripped = re.sub(r'[0-9.,\s\-–—]+', '', name_stripped).strip()
    if len(name_stripped) == 0:
        return False

    # Product name must have at least 3 characters and contain letters
    if len(name_clean) < 3 or not re.search(r'[a-zA-Z]', name_clean):
        return False
    
    # Price MUST have a currency symbol (S$, $, RM, MYR) - reject plain numbers
    if not re.search(r'(SGD|S\$|RM|MYR|\$)', price_str):
        return False
    
    # Price value must be reasonable (between 1 and 10000)
    try:
        price_match = re.search(r'([0-9]+(?:\.[0-9]{1,2})?)', price_str)
        if not price_match:
            return False
        price_val = float(price_match.group(1))
        if price_val < 1 or price_val > 10000:
            return False
    except:
        return False
    
    return True

def fetch(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r.text
    except Exception as e:
        return None

def parse_jsonld(soup):
    products = []
    for tag in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(tag.string or '')
        except Exception:
            continue
        if isinstance(data, dict):
            items = [data]
        else:
            items = data
        for it in items:
            if not isinstance(it, dict):
                continue
            if it.get('@type') and 'Product' in str(it.get('@type')):
                name = it.get('name')
                offers = it.get('offers') or {}
                price = None
                if isinstance(offers, dict):
                    price = offers.get('price') or (offers.get('priceSpecification') or {}).get('price')
                products.append({'name': name, 'price': price})
    return products

def extract_price(text):
    if not text:
        return None
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
    for cls in ['product', 'product-card', 'product-item', 'item', 'card', 'productTile']:
        try:
            candidates += soup.find_all(class_=re.compile(cls, re.I))
        except Exception:
            pass
    seen = set()
    for c in candidates:
        text = c.get_text(' ', strip=True)
        if not text or len(text) < 5:
            continue
        # require that candidate contains a price-like string or a product link
        has_price = bool(extract_price(text))
        a_tag = c.find('a', href=True)
        link_href = a_tag['href'] if a_tag else ''
        has_product_link = bool(re.search(r'(/p/|/product|/products|/item|/prod/)', link_href, re.I))
        if not (has_price or has_product_link):
            continue
        name = None
        price = None
        for tagname in ['h2','h3','h4','a','p','span']:
            t = c.find(tagname)
            if t and len(t.get_text(strip=True))>1:
                name = t.get_text(strip=True)
                break
        if not name:
            name = text.split('\n')[0][:200]
        price = extract_price(text)
        if name and is_valid_product(name, price) and (name, price) not in seen:
            seen.add((name, price))
            product_url = urljoin(base_url, link_href) if base_url and link_href else ''
            products.append({'name': name, 'price': price, 'url': product_url})
    return products

def try_search_site(domain):
    results = []
    for term in SEARCH_TERMS:
        tries = [f"{domain}/search?q={term}", f"{domain}/search?query={term}", f"{domain}/catalogsearch/result/?q={term}"]
        for url in tries:
            html = fetch(url)
            if not html:
                continue
            soup = BeautifulSoup(html, 'lxml')
            found = parse_jsonld(soup)
            if found:
                for p in found:
                    p['source'] = domain
                results.extend(found)
            cards = parse_product_cards(soup, base_url=domain)
            if cards:
                for p in cards:
                    p['source'] = domain
                results.extend(cards)
        if results:
            break
    if not results:
        html = fetch(domain)
        if html:
            soup = BeautifulSoup(html, 'lxml')
            results.extend(parse_jsonld(soup))
            results.extend(parse_product_cards(soup, base_url=domain))
    # also try finding product links directly
    html = fetch(domain)
    if html:
        soup = BeautifulSoup(html, 'lxml')
        results.extend(find_products_by_links(soup, base_url=domain))
        # follow category links if present and try parsing them
        for a in soup.find_all('a', href=True):
            href = a['href']
            if '/product-categories/' in href or '/category/' in href:
                cat_url = urljoin(domain, href)
                cat_html = fetch(cat_url)
                if not cat_html:
                    continue
                cat_soup = BeautifulSoup(cat_html, 'lxml')
                results.extend(parse_product_cards(cat_soup, base_url=domain))
                results.extend(find_products_by_links(cat_soup, base_url=domain))
    seen = set()
    out = []
    for r in results:
        key = (r.get('name'), r.get('price'))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def scrape_with_selenium(domain, search_terms):
    """Use Selenium with longer waits and scrolling to capture JS-rendered content"""
    if not SELENIUM_AVAILABLE:
        return []
    
    try:
        options = Options()
        options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-blink-features=AutomationControlled')
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        
        all_items = []
        tries = [domain] + [f"{domain}/search?q={t}" for t in search_terms]
        
        for url in tries:
            try:
                print(f'  Selenium: loading {url}')
                driver.get(url)
                # Wait longer for JS to render
                time.sleep(5)
                
                # Scroll to trigger lazy-loading
                last_height = driver.execute_script("return document.body.scrollHeight")
                for _ in range(3):
                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    time.sleep(2)
                    new_height = driver.execute_script("return document.body.scrollHeight")
                    if new_height == last_height:
                        break
                    last_height = new_height
                
                # Scroll back to top
                driver.execute_script("window.scrollTo(0, 0);")
                time.sleep(1)
                
                # Try to find products
                items = []
                seen = set()
                
                # Look for product anchors
                try:
                    anchors = driver.find_elements(By.XPATH, "//a[contains(@href,'/p/') or contains(@href,'/product') or contains(@href,'/products')]")
                    for e in anchors[:500]:
                        try:
                            name_text = e.text.strip()
                            href = e.get_attribute('href') or ''
                            parent_text = ''
                            try:
                                parent_text = e.find_element(By.XPATH, '..').text
                            except:
                                parent_text = e.text
                            price = extract_price(parent_text)
                            key = (name_text, price)
                            if name_text and key not in seen:
                                seen.add(key)
                                items.append({'name': name_text, 'price': price, 'url': href})
                        except:
                            pass
                except:
                    pass
                
                # Fallback: look for product divs
                if not items:
                    try:
                        divs = driver.find_elements(By.XPATH, "//div[contains(@class,'product') or contains(@class,'product-card') or contains(@class,'product-item') or contains(@class,'item')]")
                        for e in divs[:500]:
                            try:
                                name_text = e.text.strip()
                                parent_text = e.text
                                price = extract_price(parent_text)
                                key = (name_text, price)
                                if name_text and len(name_text) > 2 and key not in seen:
                                    seen.add(key)
                                    items.append({'name': name_text, 'price': price, 'url': ''})
                            except:
                                pass
                    except:
                        pass
                
                all_items.extend(items)
                if all_items:
                    break
            except Exception as e:
                print(f'  Error with {url}: {e}')
                continue
        
        driver.quit()
        return all_items
    except Exception as e:
        print(f'Selenium failed: {e}')
        return []


def find_products_by_links(soup, base_url=None):
    products = []
    seen = set()
    for a in soup.find_all('a', href=True):
        href = a['href']
        if re.search(r'(/p/|/product|/products|/item|/prod/)', href, re.I):
            name = a.get_text(strip=True) or a.get('title')
            # search nearby for price
            parent = a.find_parent()
            price = None
            search_scope = parent
            if parent:
                price = extract_price(parent.get_text(' ', strip=True))
            if not price:
                sib = a.find_next(string=re.compile(r'(SGD|S\$|RM|MYR|\$)'))
                if sib:
                    price = extract_price(sib)
            if not name:
                continue
            if is_valid_product(name, price):
                key = (name, price)
                if key in seen:
                    continue
                seen.add(key)
                product_url = urljoin(base_url, href) if base_url else href
                products.append({'name': name, 'price': price, 'url': product_url})
    return products

def is_price_only_name(name):
    if not name:
        return False
    cleaned = re.sub(r'(S\$|RM|\$|USD|SGD|MYR)', '', name, flags=re.I)
    cleaned = re.sub(r'[0-9.,\s\-–—]+', '', cleaned).strip()
    return len(cleaned) == 0

def normalize_name(name):
    return re.sub(r'[^a-z0-9]', '', name.lower())

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

def generate_products_csv():
    FUZZY_THRESHOLD = 0.85
    sg_products = {}
    jb_products = {}

    with open('rawdata.csv', newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row['name']
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

    for sg_name, sg_data in sg_products.items():
        if sg_name in jb_products:
            jb_data = jb_products[sg_name]
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

    jb_norms = {name: normalize_name(name) for name in jb_unmatched}
    merged_jb = set()

    for sg_name, sg_data in sg_unmatched.items():
        sg_norm = normalize_name(sg_name)
        best_score = 0
        best_jb_name = None
        for jb_name, jb_norm in jb_norms.items():
            if jb_name in merged_jb:
                continue
            score = dice_coefficient(sg_norm, jb_norm)
            if score > best_score:
                best_score = score
                best_jb_name = jb_name
        if best_score >= FUZZY_THRESHOLD and best_jb_name:
            jb_data = jb_unmatched[best_jb_name]
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

    print(f'products.csv: {len(matched)} matched products (found in both stores)')


def main():
    all_products = []
    for name, domain in SITES:
        print(f'--- Scraping {name}: {domain} ---')
        items = try_search_site(domain)
        print(f'  Static parsing: {len(items)} products')

        # Try Selenium with lower threshold for more thorough coverage
        if SELENIUM_AVAILABLE and len(items) < 50:
            print(f'  Attempting Selenium-rendered extraction (threshold: <50 products)...')
            selenium_items = scrape_with_selenium(domain, SEARCH_TERMS)
            if len(selenium_items) > len(items):
                items = selenium_items
                print(f'  Selenium found more products: {len(items)}')

        print(f'Found {len(items)} products for {name}')
        if items:
            all_products.extend([dict(site=name, **i) for i in items])

    print('\n' + '='*80)
    print('SUMMARY')
    print('='*80)
    print(f'Total products collected: {len(all_products)}')
    print()

    from collections import Counter
    site_counts = Counter(p['site'] for p in all_products)
    for site, count in site_counts.items():
        print(f'  {site}: {count} products')

    if all_products:
        cleaned_products = [
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
            writer.writerows(cleaned_products)
        print('\nRaw data saved to rawdata.csv')
        generate_products_csv()

    print()

if __name__ == '__main__':
    main()
