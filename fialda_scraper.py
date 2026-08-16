from playwright.sync_api import sync_playwright
import json
import time
import os
from collections import defaultdict

def sync_fialda_data():
    print("[Fialda Scraper] Starting sync...")
    initial_data = None
    icb_tree = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        def handle_response(response):
            nonlocal initial_data, icb_tree
            if 'api' in response.url and response.request.resource_type in ['fetch', 'xhr']:
                try:
                    if 'GetInitialData' in response.url:
                        initial_data = response.json()
                        print("[Fialda Scraper] Fetched GetInitialData.")
                    elif 'GetIcbTree' in response.url:
                        icb_tree = response.json()
                        print("[Fialda Scraper] Fetched GetIcbTree.")
                except Exception as e:
                    pass
                
        page.on('response', handle_response)
        
        print("[Fialda Scraper] Opening https://fwt.fialda.com/rrg...")
        page.goto("https://fwt.fialda.com/rrg", wait_until='networkidle', timeout=30000)
        time.sleep(5)
        
        # Try clicking to fetch GetIcbTree
        if not icb_tree:
            try:
                page.mouse.click(10, 10)
                page.keyboard.press("Escape")
                time.sleep(1)
                els = page.locator("text='Nhấn để chọn'").all()
                if els:
                    els[0].click()
                    time.sleep(3)
            except Exception as e:
                print("[Fialda Scraper] Error fetching IcbTree:", e)

        browser.close()

    if not initial_data or not icb_tree:
        print("[Fialda Scraper] Error: Missing API data")
        return False

    try:
        symbols = initial_data['result']['symbols']
        tree = icb_tree['result']
        
        # Trích xuất danh sách ngành cho frontend
        frontend_icb = []
        def extract_nodes(nodes):
            for n in nodes:
                frontend_icb.append({
                    "name": n['icbCode'],
                    "viSector": n['icbName'],
                    "icbLevel": n['icbLevel']
                })
                if 'childs' in n and n['childs']:
                    extract_nodes(n['childs'])
        
        extract_nodes(tree)

        # Save tree
        base_dir = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(base_dir, 'fialda_icb.json'), 'w', encoding='utf-8') as f:
            json.dump(frontend_icb, f, ensure_ascii=False, indent=2)
            
        # Save raw tree
        with open(os.path.join(base_dir, 'fialda_icbtree.json'), 'w', encoding='utf-8') as f:
            json.dump(icb_tree, f, ensure_ascii=False, indent=2)
            
        # Find stocks for each sector
        def get_descendants(nodes):
            desc_map = defaultdict(set)
            def collect(n):
                desc = set([n['icbCode']])
                for c in n.get('childs', []):
                    desc.update(collect(c))
                desc_map[n['icbCode']] = desc
                return desc
            for root in nodes:
                collect(root)
            return desc_map

        desc_map = get_descendants(tree)
        sector_to_stocks = defaultdict(set)

        for s in symbols:
            if s.get('type') == 'Stock' or s.get('type') == 'Fund':
                sym = s.get('symbol')
                lvl4 = s.get('icbCode_Lvl4') or s.get('icbCode')
                if not lvl4 or not sym: continue
                for parent_code, descendants in desc_map.items():
                    if lvl4 in descendants:
                        sector_to_stocks[parent_code].add(sym)

        final_map = {k: sorted(list(v)) for k, v in sector_to_stocks.items()}

        with open(os.path.join(base_dir, 'fialda_stock_mapping.json'), 'w', encoding='utf-8') as f:
            json.dump({
                "sector_to_stocks": final_map
            }, f, ensure_ascii=False, indent=2)
            
        print(f"[Fialda Scraper] Saved data for {len(final_map)} sectors.")
        return True
    except Exception as e:
        print("[Fialda Scraper] Data processing error:", e)
        return False

if __name__ == '__main__':
    sync_fialda_data()
