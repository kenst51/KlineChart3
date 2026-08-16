import sys
import os
# Fix encoding for Windows to support Vietnamese characters from vnstock
os.environ['PYTHONIOENCODING'] = 'utf-8'
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel
from typing import List, Dict, Any
import pandas as pd
from vnstock import Vnstock
import os
import urllib.request
import json
import time
import requests
from datetime import datetime, timedelta
import asyncio
from ws_manager import orderbook_manager

import math
import tempfile
import json
import os

# Tải trước bộ dữ liệu ICB cục bộ (để đạt tốc độ 0ms)
try:
    with open('sectors_mapping.json', 'r', encoding='utf-8') as f:
        sectors_mapping = json.load(f)
except Exception as e:
    print(f"Warning: Không thể đọc sectors_mapping.json: {e}")
    sectors_mapping = {}

class RRGRequest(BaseModel):
    symbols: List[str]

app = FastAPI(title="VNStock API", version="1.0.0")

# CORS config
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if not os.path.exists("static"):
    os.makedirs("static")

# Cache for symbols
_symbols_cache = []

@app.websocket("/ws/orderbook/{symbol}")
async def websocket_orderbook(websocket: WebSocket, symbol: str):
    print(f"WS CLIENT CONNECTING: {symbol}")
    await orderbook_manager.connect(websocket, symbol)
    print(f"WS CLIENT CONNECTED: {symbol}")
    try:
        while True:
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        print(f"WS CLIENT DISCONNECTED: {symbol}")
        orderbook_manager.disconnect(websocket, symbol)

@app.on_event("startup")
async def startup_event():
    global _symbols_cache
    try:
        # Fetch symbols on startup to speed up autocomplete using VNDirect API
        # Only fetch listed stocks, ETFs, IFCs, and CWs to avoid hitting the API's pagination limits
        res = requests.get('https://api-finfo.vndirect.com.vn/v4/stocks?q=type:STOCK,ETF,IFC,CW~status:listed&size=9999', headers={'User-Agent': 'Mozilla/5.0'})
        if res.status_code == 200:
            data = res.json().get('data', [])
            for row in data:
                _symbols_cache.append({
                    "symbol": row.get('code', ''),
                    "name": row.get('companyName') or row.get('shortName') or '',
                    "type": "stock" if row.get('type') == 'STOCK' else "warrant" if row.get('type') == 'CW' else "fund",
                    "exchange": row.get('floor', 'HSX')
                })
            
        # Add common indices and derivatives
        extras = [
            {"symbol": "VNINDEX", "name": "Chỉ số VN-Index", "type": "index", "exchange": "HSX"},
            {"symbol": "VN30", "name": "Chỉ số VN30", "type": "index", "exchange": "HSX"},
            {"symbol": "HNXIndex", "name": "Chỉ số HNX", "type": "index", "exchange": "HNX"},
            {"symbol": "HNX30", "name": "Chỉ số HNX30", "type": "index", "exchange": "HNX"},
            {"symbol": "UPCOMIndex", "name": "Chỉ số UPCOM", "type": "index", "exchange": "UPCOM"},
            {"symbol": "VNXALL", "name": "Chỉ số VNX AllShare", "type": "index", "exchange": "HSX"},
            {"symbol": "VN30F1M", "name": "Hợp đồng tương lai VN30F1M", "type": "future", "exchange": "HNX"},
            {"symbol": "FUEVFVND", "name": "Quỹ ETF VFMVN DIAMOND", "type": "fund", "exchange": "HSX"},
            {"symbol": "E1VFVN30", "name": "Quỹ ETF VFMVN30", "type": "fund", "exchange": "HSX"}
        ]
        
        existing_symbols = {item['symbol'] for item in _symbols_cache}
        for ext in extras:
            if ext['symbol'] not in existing_symbols:
                _symbols_cache.append(ext)
                
    except BaseException as e:
        print("Could not load symbols on startup:", e)

@app.get("/api/search-symbols")
async def search_symbols(query: str = ""):
    query = query.upper()
    if not query:
        return {"symbols": _symbols_cache[:50]}
    
    matches = []
    for s in _symbols_cache:
        s_name = str(s.get('name', '')) if s.get('name') is not None else ''
        s_symbol = str(s.get('symbol', '')).upper()
        if query in s_symbol or query in s_name.upper():
            matches.append(s)
            
    return {"symbols": matches[:20]}

@app.get("/api/watchlist-quotes")
async def get_watchlist_quotes(symbols: str = ""):
    if not symbols:
        return {"data": []}
    
    symbol_list = [s.strip().upper() for s in symbols.split(',') if s.strip()]
    
    # ── PRIMARY: VPS API (fast, batch fetch) ──────────────────────────────
    try:
        url = f"https://bgapidatafeed.vps.com.vn/getliststockdata/{','.join(symbol_list)}"
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
        if res.status_code == 200:
            data = res.json()
            if data and isinstance(data, list) and len(data) > 0:
                formatted_data = []
                for item in data:
                    sym = item.get('sym')
                    if not sym: continue
                    
                    close_price = float(item.get('lastPrice', 0) or 0)
                    prev_close  = float(item.get('r', 0) or 0)
                    ceil_price  = float(item.get('c', 0) or 0)
                    floor_price = float(item.get('f', 0) or 0)
                    change = close_price - prev_close if prev_close > 0 else float(item.get('ot', 0) or 0)
                    change_pct = (change / prev_close * 100) if prev_close > 0 else float(item.get('changePc', 0) or 0)
                    volume = float(item.get('lot', 0) or 0) * 10
                    
                    formatted_data.append({
                        "symbol": sym,
                        "close": close_price,
                        "prev_close": prev_close,
                        "ceil": ceil_price,
                        "floor": floor_price,
                        "change": round(change, 2),
                        "change_pct": round(change_pct, 2),
                        "volume": volume
                    })
                if formatted_data:
                    return {"data": formatted_data}
    except Exception as e:
        print(f"VPS watchlist error: {e}")

    # ── FALLBACK: VNDirect dchart daily for each symbol ───────────────────
    print("Watchlist: VPS failed, using VNDirect dchart fallback")
    formatted_data = []
    end_ts = int(time.time())
    start_ts = end_ts - 10 * 86400  # 10 days
    
    for sym in symbol_list:
        try:
            url = f"https://dchart-api.vndirect.com.vn/dchart/history?symbol={sym}&resolution=D&from={start_ts}&to={end_ts}"
            r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
            if r.status_code == 200:
                d = r.json()
                if d.get('s') == 'ok' and d.get('c') and len(d['c']) >= 2:
                    close_price = float(d['c'][-1])
                    prev_close  = float(d['c'][-2])
                    change = round(close_price - prev_close, 2)
                    change_pct = round((change / prev_close * 100) if prev_close > 0 else 0, 2)
                    volume = float(d.get('v', [0])[-1])
                    
                    formatted_data.append({
                        "symbol": sym,
                        "close": close_price,
                        "prev_close": prev_close,
                        "ceil": round(prev_close * 1.07, 2),
                        "floor": round(prev_close * 0.93, 2),
                        "change": change,
                        "change_pct": change_pct,
                        "volume": volume
                    })
        except Exception as sym_err:
            print(f"  fallback error for {sym}: {sym_err}")
    
    return {"data": formatted_data}

@app.post("/api/rrg")
async def get_rrg_data(req: RRGRequest):
    if not req.symbols:
        return {"rrg_data": {}}
        
    url = 'https://fwtapi1.fialda.com/api/services/app/RRG/RRGData'
    headers = {
        'appid': 'F7335346-0CB8-49A1-B9CB-A59504CBEF14',
        'sa': '184017395232524600427',
        'abp.tenantid': '6',
        'Content-Type': 'application/json;charset=UTF-8',
        'User-Agent': 'Mozilla/5.0'
    }
    
    to_time = time.time()
    from_time = to_time - 90 * 24 * 3600
    from_date = datetime.fromtimestamp(from_time).strftime('%Y-%m-%d')
    to_date = datetime.fromtimestamp(to_time).strftime('%Y-%m-%d')
    
    data = json.dumps({
        'fromDate': from_date,
        'toDate': to_date,
        'parent': 'VNINDEX',
        'symbols': req.symbols[:50],
        'icbs': [],
        'parentType': 0
    }).encode('utf-8')
    
    try:
        request = urllib.request.Request(url, data=data, headers=headers, method='POST')
        resp = urllib.request.urlopen(request, timeout=15)
        res = json.loads(resp.read().decode('utf-8'))
        raw_items = res.get('result', [])
        
        stock_series_dict = {}
        distinctColors = ['#3b82f6', '#ef4444', '#10b981', '#f59e0b', '#8b5cf6', '#06b6d4', '#ec4899', '#84cc16', '#6366f1', '#f43f5e', '#14b8a6', '#f97316', '#a855f7', '#0ea5e9', '#059669']
        
        for item in raw_items:
            dt_str = datetime.strptime(str(item['date']), '%Y%m%d').strftime('%d/%m/%Y')
            vnindex = item.get('close', 0)
            rrg_data = item.get('rrgdata', {})
            for tk, tk_data in rrg_data.items():
                ratio = tk_data.get('ratio', 100)
                mom = tk_data.get('mom', 100)
                price = tk_data.get('price', 0)
                if tk not in stock_series_dict:
                    color = distinctColors[len(stock_series_dict) % len(distinctColors)]
                    stock_series_dict[tk] = {
                        'name': tk,
                        'color': color,
                        'data': []
                    }
                stock_series_dict[tk]['data'].append([ratio, mom, dt_str, price, vnindex])
                
        for tk in stock_series_dict:
            stock_series_dict[tk]['data'] = stock_series_dict[tk]['data'][-60:]

        return {"rrg_data": stock_series_dict}
    except Exception as e:
        print('Error fetching RRG from Fialda:', e)
        return {"rrg_data": {}}

import requests

@app.get("/api/price-depth")
def get_price_depth(symbol: str):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        for center in [1, 2, 9]:
            res = requests.get(f'https://banggia.cafef.vn/stockhandler.ashx?center={center}', headers=headers, timeout=5)
            if res.status_code == 200:
                for item in res.json():
                    if item.get('a') == symbol.upper():
                        # Map theo format mà frontend mong muốn
                        depth = {
                            "bidPrice1": item.get('e'), "bidVol1": item.get('f') and item.get('f') * 10,
                            "bidPrice2": item.get('g'), "bidVol2": item.get('h') and item.get('h') * 10,
                            "bidPrice3": item.get('i'), "bidVol3": item.get('j') and item.get('j') * 10,
                            "askPrice1": item.get('s'), "askVol1": item.get('t') and item.get('t') * 10,
                            "askPrice2": item.get('q'), "askVol2": item.get('r') and item.get('r') * 10,
                            "askPrice3": item.get('o'), "askVol3": item.get('p') and item.get('p') * 10,
                            "matchPrice": item.get('l'),
                            "basicPrice": item.get('b'),
                        }
                        return JSONResponse({"status": "success", "data": [depth]})
        return JSONResponse({"status": "success", "data": []})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)})

@app.get("/api/company-overview")
def get_company_overview(symbol: str):
    from vnstock import Vnstock
    try:
        stock = Vnstock().stock(symbol=symbol, source='VCI')
        df_ov = stock.company.overview()
        
        if df_ov.empty:
            return JSONResponse({"status": "success", "data": {}})
            
        ov = df_ov.iloc[0].to_dict()
        
        # Lấy PE từ report/ratio
        pe = None
        eps = None
        try:
            df_ratio = stock.company.ratio_summary()
            if not df_ratio.empty:
                latest_ratio = df_ratio.iloc[-1].to_dict()
                pe = latest_ratio.get("pe")
                if pe and ov.get("current_price"):
                    eps = ov.get("current_price") / pe
        except:
            pass
            
        res = {
            "marketCap": ov.get("market_cap"),
            "outstandingShare": ov.get("issue_share"),
            "pe": pe,
            "eps": eps,
            "beta": None
        }
        return JSONResponse({"status": "success", "data": res})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)})

@app.get("/api/intraday")
async def get_intraday(symbol: str):
    try:
        # Lấy lịch sử giá trong ngày
        url = f"https://finfo-api.vndirect.com.vn/v4/stock_prices?q=code:{symbol.upper()}"
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers, timeout=5)
        return res.json().get('data', [{}])[0]
    except Exception as e:
        print("Error intraday:", e)
        return {}

@app.get("/api/foreign-trading")
async def get_foreign_trading(symbol: str):
    try:
        # Khối lượng giao dịch khối ngoại
        url = f"https://finfo-api.vndirect.com.vn/v4/stock_prices?q=code:{symbol.upper()}"
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers, timeout=5)
        data = res.json().get('data', [{}])[0]
        return {
            "buyVol": data.get("foreignBuyVolume"),
            "sellVol": data.get("foreignSellVolume"),
            "buyVal": data.get("foreignBuyValue"),
            "sellVal": data.get("foreignSellValue")
        }
    except Exception as e:
        print("Error foreign trading:", e)
        return {}

@app.get("/api/price-levels")
async def get_price_levels(symbol: str):
    try:
        import time
        from datetime import datetime
        
        end = int(time.time())
        start = end - 86400 * 7 # Last 7 days to ensure we get the last trading day
        
        url = f"https://dchart-api.vndirect.com.vn/dchart/history?symbol={symbol.upper()}&resolution=1&from={start}&to={end}"
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers, timeout=5)
        data = res.json()
        
        levels = {}
        if data.get('s') == 'ok':
            prices = data.get('c', [])
            vols = data.get('v', [])
            times = data.get('t', [])
            
            if times:
                last_time = max(times)
                last_date = datetime.fromtimestamp(last_time).date()
                
                for i in range(len(prices)):
                    dt = datetime.fromtimestamp(times[i]).date()
                    if dt == last_date:
                        p = round(prices[i], 2)
                        v = vols[i]
                        if p not in levels:
                            levels[p] = 0
                        levels[p] += v
                        
        sorted_levels = [{"price": k, "vol": v} for k, v in sorted(levels.items(), key=lambda x: x[0], reverse=True)]
        return JSONResponse({"status": "success", "data": sorted_levels})
    except Exception as e:
        print("Error price levels:", e)
        return JSONResponse({"status": "error", "message": str(e)})

@app.get("/api/index-overview")
def get_index_overview(symbol: str = "VNINDEX"):
    try:
        import requests
        from datetime import datetime, timedelta
        
        # 1. Market Breadth & Cash flow from CafeF
        centers = {'VNINDEX': 1, 'HNXINDEX': 2, 'UPCOMINDEX': 8, 'VN30': 9, 'HNX30': 10}
        center = centers.get(symbol.upper(), 1)
        
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(f'https://banggia.cafef.vn/stockhandler.ashx?center={center}', headers=headers, timeout=5)
        
        up = down = unchanged = 0
        up_val = down_val = unchanged_val = 0
        
        if res.status_code == 200:
            data = res.json()
            for row in data:
                ref = float(row.get('b', 0))
                match_price = float(row.get('l', 0))
                volume = float(row.get('n', 0)) # real volume = n * 10
                
                # Value in billion VND = match_price * volume / 100000
                val_billion = (match_price * volume) / 100000
                
                diff = match_price - ref if match_price > 0 else 0
                
                if diff > 0:
                    up += 1
                    up_val += val_billion
                elif diff < 0:
                    down += 1
                    down_val += val_billion
                elif match_price > 0:
                    unchanged += 1
                    unchanged_val += val_billion

        # 2. General info (Open, High, Low) from VNDirect DChart
        end_date = datetime.now()
        start_date = end_date - timedelta(days=5)
        
        unix_from = int(start_date.timestamp())
        unix_to = int(end_date.timestamp())
        
        url = f"https://dchart-api.vndirect.com.vn/dchart/history?symbol={symbol}&resolution=D&from={unix_from}&to={unix_to}"
        res_vnd = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
        
        info = {}
        if res_vnd.status_code == 200:
            json_data = res_vnd.json()
            if json_data.get('s') == 'ok' and len(json_data.get('c', [])) > 0:
                # The last one is the latest
                info = {
                    'open': float(json_data['o'][-1]),
                    'high': float(json_data['h'][-1]),
                    'low': float(json_data['l'][-1]),
                    'close': float(json_data['c'][-1]),
                    'matched_volume': float(json_data['v'][-1])
                }
            
        # 3. Total Volume and Total Value from CafeF index
        res_idx = requests.get('https://banggia.cafef.vn/stockhandler.ashx?index=true', headers=headers, timeout=5)
        if res_idx.status_code == 200:
            idx_data = res_idx.json()
            for idx in idx_data:
                if idx.get('name') == symbol.upper():
                    info['total_volume'] = float(str(idx.get('volume', '0')).replace(',', ''))
                    info['total_value'] = float(str(idx.get('value', '0')).replace(',', ''))
                    break
        
        # Calculate deal volume (thỏa thuận)
        matched = info.get('matched_volume', 0)
        total = info.get('total_volume', matched)
        info['deal_volume'] = max(0, total - matched)
        info['matched_volume'] = matched
        
        # Optional: Foreign trades. Leave empty for now, will handle later.
        
        return {
            'info': info,
            'breadth': {
                'up': up, 'down': down, 'unchanged': unchanged
            },
            'cash_flow': {
                'up_val': round(up_val, 2),
                'down_val': round(down_val, 2),
                'unchanged_val': round(unchanged_val, 2)
            }
        }
    except Exception as e:
        print("Error index overview:", e)
        return {}

@app.get("/api/financial_ratios")
def get_financial_ratios(symbol: str):
    from vnstock import Fundamental
    import pandas as pd
    import numpy as np
    
    try:
        f = Fundamental().equity(symbol)
        df_q = f.ratio(period='quarter')
        df_y = f.ratio(period='year')
        
        df_q = df_q.replace([np.inf, -np.inf, np.nan], None)
        df_y = df_y.replace([np.inf, -np.inf, np.nan], None)
        
        # Nhóm các chỉ số theo yêu cầu
        groups = {
            "Định giá": ["P/E", "P/B", "P/S", "Giá trị sổ sách của cổ phiếu (BVPS)", "Tỷ suất cổ tức", "Beta", "Giá trị doanh nghiệp trên lợi nhuận trước thuế và lãi vay (EV/EBIT)", "Giá trị doanh nghiệp trên lợi nhuận trước thuế, khấu hao và lãi vay (EV/EBITDA)"],
            "Biên lợi nhuận": ["ROA bình quân 4 quý gần nhất", "ROE bình quân 4 quý gần nhất", "Tỷ suất sinh lợi trên vốn dài hạn bình quân (ROCE)", "Tỷ suất lợi nhuận gộp biên", "Tỷ lệ lãi EBIT", "Tỷ lệ lãi EBITDA", "Tỷ suất sinh lợi trên doanh thu thuần", "Tỷ suất lợi nhuận trên vốn chủ sở hữu bình quân (ROEA)", "Tỷ suất sinh lợi trên tổng tài sản bình quân (ROAA)"],
            "Tăng trưởng": ["Tăng trưởng  doanh thu thuần", "Tăng trưởng  lợi nhuận gộp", "Tăng trưởng lợi nhuận sau thuế của CĐ công ty mẹ", "Tăng trưởng lợi nhuận trước thuế ", "Tăng trưởng tổng tài sản", "Tăng trưởng vốn chủ sở hữu", "Tăng trưởng vốn điều lệ"],
            "Thanh khoản & Hiệu quả": ["Tỷ số thanh toán hiện hành (ngắn hạn)", "Tỷ số thanh toán nhanh", "Tỷ số thanh toán bằng tiền mặt", "Vòng quay hàng tồn kho", "Vòng quay phải thu khách hàng", "Vòng quay tổng tài sản (Hiệu suất sử dụng toàn bộ tài sản)", "Vòng quay tài sản cố định (Hiệu suất sử dụng tài sản cố định)"],
            "Đòn bẩy tài chính": ["Tỷ số Nợ trên Tổng tài sản", "Tỷ số Nợ vay trên Vốn chủ sở hữu", "Khả năng thanh toán lãi vay", "Tỷ số Nợ trên Vốn chủ sở hữu", "Tỷ số Nợ vay trên Tổng tài sản"],
            "Dòng tiền": ["Tỷ số dòng tiền HĐKD trên doanh thu thuần", "Dòng tiền từ HĐKD trên Tổng tài sản", "Dòng tiền từ HĐKD trên mỗi cổ phần (CPS)", "Dòng tiền từ HĐKD trên Vốn chủ sở hữu"]
        }
        
        def process_df(df):
            if df.empty: return {"groups": [], "periods": []}
            
            # Lọc bỏ các cột bị trùng lặp do lỗi của vnstock (chứa dấu _) và sắp xếp giảm dần (mới nhất đầu tiên)
            raw_periods = [col for col in df.columns if col not in ['item', 'item_id'] and '_' not in col]
            periods = sorted(raw_periods, reverse=True)
            
            result_groups = []
            for g_name, g_items in groups.items():
                group_data = []
                for item_name in g_items:
                    # Tìm hàng tương ứng bằng chứa chuỗi (contains) vì có khoảng trắng thừa
                    row = df[df['item'].str.contains(item_name.replace(" ", ".*").replace("(", "\\(").replace(")", "\\)"), regex=True, na=False)]
                    if not row.empty:
                        r = row.iloc[0]
                        # Bỏ qua nếu tất cả các kỳ đều là 0 hoặc None (vnstock không tính toán)
                        is_all_zero_or_none = True
                        for p in periods:
                            val = r[p]
                            if val not in [None, 0, 0.0, "0", "0.0", ""]:
                                is_all_zero_or_none = False
                                break
                                
                        if not is_all_zero_or_none:
                            group_data.append({
                                "name": r['item'],
                                "values": {p: r[p] for p in periods}
                            })
                if group_data:
                    result_groups.append({
                        "group_name": g_name,
                        "items": group_data
                    })
            return {"groups": result_groups, "periods": periods}

        return {
            "quarterly": process_df(df_q),
            "yearly": process_df(df_y)
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"error": str(e)}

_cafef_cache = {}
@app.get("/api/cafef_financials")
async def get_cafef_financials(symbol: str, period: str = "quarterly", report_type: str = "ALL"):
    try:
        import time
        global _cafef_cache
        
        cache_key = f"{symbol}_{period}_{report_type}_v5"
        now = time.time()
        # Cache 24h (86400 seconds)
        if cache_key in _cafef_cache and now - _cafef_cache[cache_key]['time'] < 86400:
            return _cafef_cache[cache_key]['data']
            
        import requests
        type_time = "QUY" if period == "quarterly" else "NAM"
        
        r_type = report_type if report_type != "CSTC" else "ALL"
        url1 = f"https://apiweb.cafef.vn/api/v1/BCTC/GetReportSummary?symbol={symbol}&pageIndex=1&pageSize=100&reportType={r_type}&TypeTime={type_time}"
        url2 = f"https://apiweb.cafef.vn/api/v2/BCTC/FinancialIndicators?symbol={symbol}&pageIndex=1&pageSize=100"
        url3 = f"https://apiweb.cafef.vn/api/v1/BCTC/GetReportLCTT?symbol={symbol}&pageIndex=1&pageSize=100&reportType=ALL&TypeTime={type_time}"
        
        headers = {'User-Agent': 'Mozilla/5.0'}
        res1 = requests.get(url1, headers=headers).json() if report_type not in ["CSTC", "LCTT"] else {}
        res2 = requests.get(url2, headers=headers).json() if report_type in ["ALL", "CSTC"] else {}
        res3 = requests.get(url3, headers=headers).json() if report_type in ["ALL", "LCTT"] else {}
        
        groups = []
        periods = []
        for res_obj in [res1, res3]:
            if "value" in res_obj and "templace" in res_obj["value"]:
                # Lấy danh sách các kỳ (đảo ngược để hiển thị mới nhất trước)
                if not periods:
                    for d in res_obj["value"].get("data", []):
                        if d.get("data"):
                            periods = [f"{p['time']}" for p in reversed(d["data"])]
                            break
                
                for temp in res_obj["value"].get("templace", []):
                    group_name = temp.get("name", "")
                    group_code = temp.get("code", "")
                    
                    bctc_data = next((x for x in res_obj["value"].get("data", []) if x.get("code") == group_code), None)
                    if not bctc_data or not bctc_data.get("data"): continue
                    
                    group_items = []
                    for row_def in temp.get("data", []):
                        row_name = row_def.get("name", "")
                        row_code = row_def.get("code", "")
                        row_values = {}
                        
                        is_all_null = True
                        for i, p_data in enumerate(reversed(bctc_data.get("data", []))):
                            if i < len(periods):
                                period_name = periods[i]
                                val = next((v.get("value") for v in p_data.get("data", []) if v.get("code") == row_code), None)
                                row_values[period_name] = val
                                if val not in [None, 0, 0.0, "0", "0.0", ""]:
                                    is_all_null = False
                                
                        if not is_all_null:
                            group_items.append({
                                "name": row_name,
                                "values": row_values
                            })
                    
                    if group_items:
                        groups.append({
                            "group_name": group_name,
                            "items": group_items
                        })
                        
        if report_type in ["ALL", "CSTC"]:
            # Lấy Chỉ số tài chính từ res2
            if "value" in res2 and "templace" in res2["value"]:
                temp_list = res2["value"]["templace"]
                bctc_data = res2["value"]["data"]
                
                if temp_list and bctc_data:
                    if not periods:
                        periods = [f"{p['time']}" for p in reversed(bctc_data)]
                        
                    group_items = []
                    for row_def in temp_list:
                        row_name = row_def["name"]
                        row_code = row_def["code"]
                        row_values = {}
                        
                        is_all_null = True
                        for i, p_data in enumerate(reversed(bctc_data)):
                            if i < len(periods):
                                period_name = periods[i]
                                val = next((v["value"] for v in p_data.get("data", []) if v["code"] == row_code), None)
                                row_values[period_name] = val
                                if val not in [None, 0, 0.0, "0", "0.0", ""]:
                                    is_all_null = False
                                    
                        if not is_all_null:
                            group_items.append({
                                "name": row_name,
                                "values": row_values
                            })
                            
                    if group_items:
                        groups.append({
                            "group_name": res2["value"].get("name", "Chỉ số tài chính"),
                            "items": group_items
                        })
                        
        result = {
            "periods": periods,
            "groups": groups
        }
        
        _cafef_cache[cache_key] = {'time': now, 'data': result}
        return result
    except Exception as e:
        return {"error": str(e), "periods": [], "groups": []}

@app.get("/api/valuation-chart")
def get_valuation_chart(symbol: str):
    from vnstock import Fundamental
    import pandas as pd
    from datetime import datetime, timedelta
    
    try:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=3*365)
        
        # 1. Fetch daily prices using VNDirect
        unix_from = int(start_date.timestamp())
        unix_to = int(end_date.timestamp())
        
        url = f"https://dchart-api.vndirect.com.vn/dchart/history?symbol={symbol}&resolution=D&from={unix_from}&to={unix_to}"
        res_vnd = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
        if res_vnd.status_code != 200:
            return {"status": "error", "message": "Failed to fetch from VNDirect DChart API"}
            
        json_data = res_vnd.json()
        if json_data.get('s') != 'ok':
            return {"status": "error", "message": "No price data"}
            
        # Convert to DataFrame
        t_arr = json_data.get('t', [])
        c_arr = json_data.get('c', [])
        
        data_rows = []
        for i in range(len(t_arr)):
            data_rows.append({
                "time": datetime.fromtimestamp(t_arr[i]).strftime("%Y-%m-%d"),
                "close": c_arr[i]
            })
            
        df_price = pd.DataFrame(data_rows)
        
        # 2. Fetch quarterly data from Vietcap APIs
        url_fs = f"https://iq.vietcap.com.vn/api/iq-insight-service/v1/company/{symbol}/financial-statement?section=INCOME_STATEMENT"
        url_stat = f"https://iq.vietcap.com.vn/api/iq-insight-service/v1/company/{symbol}/statistics-financial"
        
        headers = {'User-Agent': 'Mozilla/5.0'}
        res_fs = requests.get(url_fs, headers=headers).json()
        res_stat = requests.get(url_stat, headers=headers).json()
        
        if not res_fs.get('successful') or not res_stat.get('successful'):
            return {"status": "error", "message": "Failed to fetch data from Vietcap API"}
            
        # Process INCOME_STATEMENT for LNST (isa22)
        qs_fs = res_fs.get('data', {}).get('quarters', [])
        if not qs_fs:
            return {"status": "error", "message": "No income statement data"}
            
        df_fs = pd.DataFrame(qs_fs)
        df_fs['q_date'] = pd.to_datetime(df_fs['yearReport'].astype(str) + '-' + (df_fs['lengthReport'] * 3).astype(str) + '-01')
        df_fs['LNST'] = df_fs.get('isa22', 0)
        df_fs = df_fs.sort_values('q_date')
        df_fs['LNST_TTM'] = df_fs['LNST'].rolling(4).sum()
        df_fs['quarter'] = df_fs['lengthReport']
        
        # Process statistics-financial for market_cap, pb, number_of_shares_mkt_cap
        qs_stat = res_stat.get('data', [])
        if not qs_stat:
            return {"status": "error", "message": "No statistics data"}
            
        df_stat = pd.DataFrame(qs_stat)
        df_stat = df_stat[df_stat['ratioType'] == 'RATIO_TTM'].copy()
        
        def get_quarter_end(y, q):
            if q == 1: return f'{y}-03-31'
            if q == 2: return f'{y}-06-30'
            if q == 3: return f'{y}-09-30'
            return f'{y}-12-31'
            
        df_stat['q_date_stat'] = pd.to_datetime(df_stat.apply(lambda row: get_quarter_end(row['yearReport'], row['quarter']), axis=1))
        
        # Merge the two datasets on yearReport and quarter
        df_q = pd.merge(df_stat, df_fs[['yearReport', 'quarter', 'LNST_TTM']], on=['yearReport', 'quarter'], how='inner')
        df_q['q_date'] = df_q['q_date_stat']
        
        # Calculate Total Earnings (TTM) and Total Equity for that quarter
        df_q['total_earnings'] = df_q['LNST_TTM']
        df_q['total_equity'] = df_q['marketCap'] / df_q['pb']
        df_q['number_of_shares_mkt_cap'] = df_q['numberOfSharesMktCap']
        
        df_q = df_q.sort_values('q_date').dropna(subset=['total_earnings', 'total_equity'])
        
        # 3. Merge asof (assign each day the latest available quarter data before it)
        df_price['time'] = pd.to_datetime(df_price['time'])
        df_price = df_price.sort_values('time')
        merged = pd.merge_asof(df_price, df_q[['q_date', 'total_earnings', 'total_equity', 'number_of_shares_mkt_cap']], left_on='time', right_on='q_date', direction='backward')
        
        # 4. Calculate daily PE, PB, Market Cap
        # merged['close'] is typically in 1000 VND (e.g., 90.5 for 90,500)
        # number_of_shares_mkt_cap is actual shares.
        # df_q['market_cap'] in ratio_summary is actual VND (e.g., 500,000,000,000,000)
        # total_earnings and total_equity are actual VND.
        
        merged['daily_market_cap_actual'] = (merged['close'] * 1000) * merged['number_of_shares_mkt_cap']
        
        merged['daily_pe'] = merged['daily_market_cap_actual'] / merged['total_earnings']
        merged['daily_pb'] = merged['daily_market_cap_actual'] / merged['total_equity']
        
        # Convert daily market cap to Billion VND for display
        merged['daily_market_cap_billion'] = merged['daily_market_cap_actual'] / 1e9
        
        # Replace Infinity with NaN and drop invalid rows
        import numpy as np
        merged = merged.replace([np.inf, -np.inf], np.nan)
        merged = merged.dropna(subset=['daily_pe', 'daily_pb'])
        # Format response
        res_data = []
        for _, row in merged.iterrows():
            res_data.append({
                "time": row['time'].strftime('%Y-%m-%d'),
                "pe": round(row['daily_pe'], 2),
                "pb": round(row['daily_pb'], 2),
                "market_cap": round(row['daily_market_cap_billion'], 2)
            })
                
        try:
            os.makedirs("cache", exist_ok=True)
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(res_data, f)
        except:
            pass
            
        return {"status": "success", "data": res_data}
    except BaseException as e:
        import traceback
        traceback.print_exc()
        error_msg = str(e)
        if isinstance(e, SystemExit):
            error_msg = "Vnstock API Rate Limit Exceeded. Hãy đăng nhập vnstocks.com để lấy API key hoặc đợi 1 phút."
        return {"status": "error", "message": error_msg}


class QuotesRequest(BaseModel):
    symbols: List[str]

@app.get("/api/cafef_overview")
def get_cafef_overview(symbol: str, cafef_url: str = None):
    import requests
    import json
    
    symbol = symbol.strip().upper()
    
    # 1. Fetch URL if provided to act as a proper client
    if cafef_url:
        try:
            requests.get(cafef_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
        except Exception:
            pass

    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    
    data = {
        "ThamChieu": "-", "Tran": "-", "San": "-", "MoCua": "-", "CaoNhat": "-", "ThapNhat": "-",
        "NNMuaKL": "-", "NNMuaGT": "-", "NNBanKL": "-", "NNBanGT": "-", "Room": "-",
        "EPSCoBan": "-", "EPSPhaLoang": "-", "PE": "-", "GiaTriSoSach": "-", "PB": "-",
        "VonHoa": "-", "KLGD10Phien": "-", "KLCPNiemYet": "-", "KLCPLuuHanh": "-",
        "NhomNganh": "-"
    }
    
    def format_num(val):
        if val is None: return "-"
        try:
            # Format with dot as thousands separator
            return f"{float(val):,.0f}".replace(",", ".")
        except:
            return str(val)
            
    def format_billion(val):
        if val is None: return "-"
        try:
            # Convert to billions and keep 2 decimal places
            billions = float(val) / 1000000000
            return f"{billions:.2f}"
        except:
            return str(val)

    # 2. API for Price & Volume
    price_url = f"https://cafef.vn/du-lieu/Ajax/PageNew/RealtimePrice.ashx?Symbol={symbol}"
    try:
        res_p = requests.get(price_url, headers=headers, timeout=5)
        if res_p.status_code == 200:
            p_json = res_p.json()
            if p_json.get("Success") and p_json.get("Data"):
                p_data = p_json["Data"]
                data["ThamChieu"] = str(p_data.get("GiaThamChieu", "-"))
                data["Tran"] = str(p_data.get("GiaTran", "-"))
                data["San"] = str(p_data.get("GiaSan", "-"))
                data["MoCua"] = str(p_data.get("GiaMoCua", "-"))
                data["CaoNhat"] = str(p_data.get("GiaCaoNhat", "-"))
                data["ThapNhat"] = str(p_data.get("GiaThapNhat", "-"))
                
                data["NNMuaKL"] = format_num(p_data.get("KhoiLuongNNMua"))
                data["NNMuaGT"] = format_billion(p_data.get("GiaTriNNMua"))
                data["NNBanKL"] = format_num(p_data.get("KhoiLuongNNBan"))
                data["NNBanGT"] = format_billion(p_data.get("GiaTriNNBan"))
                
                room = p_data.get("RoomConLai")
                if room is not None:
                    data["Room"] = f"{float(room):.2f} (%)"
    except Exception as e:
        print(f"Error fetching price: {e}")
        pass
        
    # 3. API for Financial Indicators
    finance_url = f"https://cafef.vn/du-lieu/Ajax/PageNew/ChiSoTaiChinh.ashx?Symbol={symbol}"
    try:
        res_f = requests.get(finance_url, headers=headers, timeout=5)
        if res_f.status_code == 200:
            f_json = res_f.json()
            if f_json.get("Success") and f_json.get("Data"):
                for item in f_json["Data"]:
                    code = item.get("Code", "")
                    val = item.get("Value", "-").strip()
                    if val == "": val = "-"
                    
                    if code == "EPScoBan": data["EPSCoBan"] = val
                    elif code == "EPSphaLoang": data["EPSPhaLoang"] = val
                    elif code == "P/E": data["PE"] = val
                    elif code == "GiaTriSoSach": data["GiaTriSoSach"] = val
                    elif code == "Beta": data["PB"] = val
                    elif code == "VonHoaThiTruong": data["VonHoa"] = val
                    elif code == "KhopLenh10Phien": data["KLGD10Phien"] = val
                    elif code == "KlcpNY": data["KLCPNiemYet"] = val
                    elif code == "KlcpLuuHanh": data["KLCPLuuHanh"] = val
    except Exception as e:
        print(f"Error fetching finance info: {e}")
        pass

    # 4. Fetch Industry from companyinfor
    try:
        info_url = f"https://cafef.vn/du-lieu/ajax/pagenew/companyinfor.ashx?symbol={symbol}"
        res_info = requests.get(info_url, headers=headers, timeout=5)
        if res_info.status_code == 200:
            info_json = res_info.json()
            if "Data" in info_json and "Nganh" in info_json["Data"]:
                data["NhomNganh"] = info_json["Data"]["Nganh"]
    except Exception as e:
        print(f"Error fetching company info for {symbol}: {e}")

    return data

@app.post("/api/export_cafef")
def export_cafef_data(req_data: dict):
    from fastapi.responses import Response, StreamingResponse
    import io
    import pandas as pd
    import re
    
    try:
        format = req_data.get('format', 'csv')
        cafef_data = req_data.get('data', {})
        symbol = cafef_data.get('symbol', 'UNKNOWN')
        periods = cafef_data.get('periods', [])
        groups = cafef_data.get('groups', [])
        
        rows = []
        for group in groups:
            for item in group.get('items', []):
                row = {'Chỉ tiêu': item.get('name', '')}
                item_values = item.get('values', {})
                for p in periods:
                    val = item_values.get(p, "")
                    row[p] = val
                rows.append(row)
                
        df = pd.DataFrame(rows)
        
        if format == 'csv':
            csv_data = df.to_csv(index=False, encoding='utf-8-sig')
            return Response(
                content=csv_data, 
                media_type="text/csv", 
                headers={"Content-Disposition": f"attachment; filename={symbol}_BCTC.csv"}
            )
            
        elif format == 'xlsx':
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name=symbol)
            output.seek(0)
            return StreamingResponse(
                output, 
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 
                headers={"Content-Disposition": f"attachment; filename={symbol}_BCTC.xlsx"}
            )
            
        elif format == 'dta':
            def clean_stata_col(c):
                if c == 'Chỉ tiêu': return 'Chi_tieu'
                c = re.sub(r'[^a-zA-Z0-9_]', '_', c)
                if c and c[0].isdigit(): c = '_' + c
                return c
                
            df.columns = [clean_stata_col(c) for c in df.columns]
            
            output = io.BytesIO()
            df.to_stata(output, write_index=False, version=114)
            output.seek(0)
            return StreamingResponse(
                output, 
                media_type="application/octet-stream", 
                headers={"Content-Disposition": f"attachment; filename={symbol}_BCTC.dta"}
            )
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"error": str(e)}


_fundamental_cache = {}
@app.get("/api/fundamental")
async def get_fundamental(symbol: str):
    import time
    import requests
    global _fundamental_cache
    
    symbol = symbol.upper()
    cache_key = symbol
    now = time.time()
    
    if cache_key in _fundamental_cache and now - _fundamental_cache[cache_key]['time'] < 3600:
        return _fundamental_cache[cache_key]['data']
        
    headers = {'User-Agent': 'Mozilla/5.0'}
    url1 = f"https://iq.vietcap.com.vn/api/iq-insight-service/v1/company/{symbol}/financial-statement?section=INCOME_STATEMENT"
    url2 = f"https://iq.vietcap.com.vn/api/iq-insight-service/v1/company/{symbol}/statistics-financial"
    
    try:
        r1 = requests.get(url1, headers=headers, timeout=10)
        r2 = requests.get(url2, headers=headers, timeout=10)
        
        d1 = r1.json() if r1.status_code == 200 else {}
        d2 = r2.json() if r2.status_code == 200 else {}
        
        data1 = d1.get("data") or {}
        data2 = d2.get("data") or []
        
        years = data1.get("years", [])
        quarters = data1.get("quarters", [])
        stats = data2 if isinstance(data2, list) else []
        
        # Lấy 20 quý gần nhất và 6 năm gần nhất (để tính YoY cho năm thứ 5)
        quarters = quarters[-20:] if len(quarters) > 20 else quarters
        years = years[-6:] if len(years) > 6 else years
        
        # Helper map stats
        # stats có quarter, year, pe, roe, eps...
        # Map stats by period
        
        result = {
            "years": years,
            "quarters": quarters,
            "stats": stats
        }
        
        _fundamental_cache[cache_key] = {'time': now, 'data': result}
        return result
    except Exception as e:
        return {"error": str(e)}

_interest_rates_cache = {}
@app.get("/api/interest-rates")
async def get_interest_rates():
    import time
    import json
    global _interest_rates_cache
    
    now = time.time()
    if 'data' in _interest_rates_cache and now - _interest_rates_cache['time'] < 3600:
        return _interest_rates_cache['data']
        
    try:
        # Đọc lai_suat_all.json
        filepath = os.path.join(os.path.dirname(__file__), "lai_suat_all.json")
        if not os.path.exists(filepath):
            return {"error": "File not found"}
            
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        # Lọc InterestTermID == 14 (12 tháng)
        filtered = [item for item in data if item.get("InterestTermID") == 14]
        
        _interest_rates_cache = {'time': now, 'data': filtered}
        return filtered
    except Exception as e:
        return {"error": str(e)}


_company_info_cache = {}

def fetch_vietcap_api(endpoint: str, symbol: str, cache_type: str):
    import time
    import requests
    global _company_info_cache
    
    cache_key = f"{symbol}_{cache_type}"
    now = time.time()
    
    # Cache 30 mins
    if cache_key in _company_info_cache and now - _company_info_cache[cache_key]['time'] < 1800:
        return _company_info_cache[cache_key]['data']
        
    url = f"https://iq.vietcap.com.vn/api/iq-insight-service/{endpoint}"
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            _company_info_cache[cache_key] = {'time': now, 'data': data}
            return data
        return {"error": f"API returned status {res.status_code}"}
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/company-details")
def get_company_details(symbol: str):
    return fetch_vietcap_api(f"v1/company/details?ticker={symbol.upper()}", symbol, "details")

@app.get("/api/company-shareholder-structure")
def get_company_shareholder_structure(symbol: str):
    return fetch_vietcap_api(f"v1/company/{symbol.upper()}/shareholder-structure", symbol, "shareholder-structure")

@app.get("/api/company-shareholders")
def get_company_shareholders(symbol: str):
    return fetch_vietcap_api(f"v1/company/{symbol.upper()}/shareholder", symbol, "shareholders")

@app.get("/api/company-relationships")
def get_company_relationships(symbol: str):
    return fetch_vietcap_api(f"v1/company/{symbol.upper()}/relationship", symbol, "relationships")

@app.get("/api/company-events")
def get_company_events(symbol: str):
    from datetime import datetime, timedelta
    symbol = symbol.upper()
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=365*2)).strftime('%Y-%m-%d')
    return fetch_vietcap_api(f"v1/events?ticker={symbol}&fromDate={start_date}&toDate={end_date}", symbol, "events")

@app.get("/api/company-news")
def get_company_news(symbol: str, page: int = 0, size: int = 20):
    # Vietcap API is 0-indexed for pages
    from datetime import datetime, timedelta
    symbol = symbol.upper()
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=365*2)).strftime('%Y-%m-%d')
    return fetch_vietcap_api(f"v1/news?ticker={symbol}&fromDate={start_date}&toDate={end_date}&page={page}&size={size}", f"{symbol}_p{page}", "news")

@app.get('/api/realtime-prices')
def get_realtime_prices(symbols: str):
    import requests
    from datetime import datetime, timedelta
    try:
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        url = f'https://api-finfo.vndirect.com.vn/v4/stock_prices?sort=-date&q=code:{symbols}~date:gte:{start_date}~date:lte:{end_date}&size=100'
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
        if res.status_code == 200:
            return res.json()
        return {'data': []}
    except Exception as e:
        return {'data': []}

import pandas_ta as ta

@app.get("/api/technical-signals")
async def get_technical_signals(symbol: str, resolution: str = "D"):
    try:
        import requests
        from datetime import datetime, timedelta
        import time
        import math
        
        symbol = symbol.upper()
        end = int(time.time())
        
        is_weekly = resolution.upper() == "W"
        fetch_res = "D" if is_weekly else resolution
        
        # Require 5 years for Weekly to calculate 200 SMA (~260 weeks), and 2 years for Daily (~500 days)
        years_back = 5 if is_weekly else 2
        start = end - years_back * 365 * 86400
        
        url = f"https://dchart-api.vndirect.com.vn/dchart/history?symbol={symbol}&resolution={fetch_res}&from={start}&to={end}"
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers, timeout=5)
        
        try:
            data = res.json()
        except Exception as e:
            return JSONResponse({"status": "error", "message": f"API Error: {str(e)}"})
            
        if data.get('s') != 'ok':
            return JSONResponse({"status": "error", "message": "No data"})
            
        df = pd.DataFrame({
            'time': pd.to_datetime(data['t'], unit='s'),
            'open': data['o'],
            'high': data['h'],
            'low': data['l'],
            'close': data['c'],
            'volume': data['v']
        })
        
        if is_weekly:
            df.set_index('time', inplace=True)
            df = df.resample('W-FRI').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna()
            df.reset_index(inplace=True)
        
        # Calculate indicators
        df.ta.rsi(length=14, append=True)
        df.ta.stoch(k=14, d=3, smooth_k=1, append=True)
        
        # Calculate STOCHRSI manually according to the standard raw formula
        # Note: TCBS uses n=5 for STOCHRSI_FASTK (both daily and weekly)
        rsi_14 = df['RSI_14']
        lowest_rsi = rsi_14.rolling(5).min()
        highest_rsi = rsi_14.rolling(5).max()
        df['STOCHRSI_FASTK'] = 100 * (rsi_14 - lowest_rsi) / (highest_rsi - lowest_rsi)

        df.ta.macd(fast=12, slow=26, signal=9, append=True)
        df.ta.adx(length=14, append=True)
        df.ta.willr(length=14, append=True)
        
        # Calculate CCI manually due to pandas-ta bug with mad()
        import numpy as np
        tp = (df['high'] + df['low'] + df['close']) / 3
        sma_tp = tp.rolling(20).mean()
        mad = tp.rolling(20).apply(lambda x: np.mean(np.abs(x - x.mean())))
        df['CCI_20'] = (tp - sma_tp) / (0.015 * mad)
        
        df.ta.roc(length=9, append=True)
        df.ta.psar(af0=0.02, af=0.02, max_af=0.2, append=True)
        df.ta.uo(fast=7, medium=14, slow=28, append=True)
        df.ta.bbands(length=20, append=True)
        
        for p in [5, 10, 20, 50, 100, 200]:
            df.ta.sma(length=p, append=True)
            df.ta.ema(length=p, append=True)
            
        if len(df) < 2:
            return JSONResponse({"status": "error", "message": "Not enough data"})
            
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        price = latest['close']
        
        def safe_val(val):
            if pd.isna(val) or math.isnan(val) or math.isinf(val):
                return None
            return float(val)
            
        def safe_round(val, decimals=2):
            v = safe_val(val)
            return round(v, decimals) if v is not None else None

        def get_trend(curr, prev):
            if curr is None or prev is None: return 0
            if curr > prev: return 1
            if curr < prev: return -1
            return 0
            
        psar_val = safe_val(latest.get('PSARl_0.02_0.2', None))
        if psar_val is None:
            psar_val = safe_val(latest.get('PSARs_0.02_0.2', None))
            
        vals = {
            'RSI': safe_round(latest.get('RSI_14')),
            'STOCHK': safe_round(latest.get('STOCHk_14_3_1')),
            'STOCHRSI_FASTK': safe_round(latest.get('STOCHRSI_FASTK')),
            'MACD': safe_round(latest.get('MACD_12_26_9')),
            'MACD_SIGNAL': safe_round(latest.get('MACDs_12_26_9')),
            'MACD_HISTOGRAM': safe_round(latest.get('MACDh_12_26_9')),
            'ADX': safe_round(latest.get('ADX_14')),
            'DMP': safe_round(latest.get('DMP_14')),
            'DMN': safe_round(latest.get('DMN_14')),
            'WPR': safe_round(latest.get('WILLR_14')),
            'CCI': safe_round(latest.get('CCI_20')),
            'ROC': safe_round(latest.get('ROC_9')),
            'SAR': safe_round(psar_val),
            'ULTOSC': safe_round(latest.get('UO_7_14_28')),
            'BB_WIDTH': safe_round(latest.get('BBB_20_2.0_2.0') / 100 if latest.get('BBB_20_2.0_2.0') is not None else None),
            
            'MACD_HISTOGRAM_DIR': get_trend(latest.get('MACDh_12_26_9'), prev.get('MACDh_12_26_9')),
            'ADX_DIR': get_trend(price, prev['close']),
            'ROC_DIR': get_trend(latest.get('ROC_9'), prev.get('ROC_9')),
            'BB_WIDTH_DIR': get_trend(latest.get('BBB_20_2.0_2.0'), prev.get('BBB_20_2.0_2.0')),
            'PRICE_DIR': get_trend(price, prev['close'])
        }
        
        mas = {}
        for p in [5, 10, 20, 50, 100, 200]:
            mas[f'SMA_{p}'] = safe_round(latest.get(f'SMA_{p}'))
            mas[f'EMA_{p}'] = safe_round(latest.get(f'EMA_{p}'))
            
        def is_approx(a, b, tol=0.001):
            if a is None or b is None: return False
            return abs(a - b) / a <= tol

        def classify_tab1(vals):
            signals = {}
            v = vals['RSI']
            if v is None: signals['RSI'] = 'TRUNG_TINH'
            elif v > 70: signals['RSI'] = 'MUA'
            elif v < 30: signals['RSI'] = 'BAN'
            else: signals['RSI'] = 'TRUNG_TINH'
            
            v = vals['STOCHK']
            if v is None: signals['STOCHK'] = 'TRUNG_TINH'
            elif v > 80: signals['STOCHK'] = 'MUA'
            elif v < 20: signals['STOCHK'] = 'BAN'
            else: signals['STOCHK'] = 'TRUNG_TINH'
            
            v = vals['STOCHRSI_FASTK']
            if v is None: signals['STOCHRSI_FASTK'] = 'TRUNG_TINH'
            elif v > 80: signals['STOCHRSI_FASTK'] = 'MUA'
            elif v < 20: signals['STOCHRSI_FASTK'] = 'BAN'
            else: signals['STOCHRSI_FASTK'] = 'TRUNG_TINH'
            
            v = vals['WPR']
            if v is None: signals['WPR'] = 'TRUNG_TINH'
            elif v > -20: signals['WPR'] = 'MUA'
            elif v < -80: signals['WPR'] = 'BAN'
            else: signals['WPR'] = 'TRUNG_TINH'
            
            v = vals['CCI']
            if v is None: signals['CCI'] = 'TRUNG_TINH'
            elif v > 100: signals['CCI'] = 'MUA'
            elif v < -100: signals['CCI'] = 'BAN'
            else: signals['CCI'] = 'TRUNG_TINH'
            
            v = vals['ULTOSC']
            if v is None: signals['ULTOSC'] = 'TRUNG_TINH'
            elif v > 70: signals['ULTOSC'] = 'MUA'
            elif v < 30: signals['ULTOSC'] = 'BAN'
            else: signals['ULTOSC'] = 'TRUNG_TINH'
            
            m, s = vals['MACD'], vals['MACD_SIGNAL']
            if m is None or s is None or m == s: signals['MACD'] = 'TRUNG_TINH'
            elif m > s: signals['MACD'] = 'MUA'
            else: signals['MACD'] = 'BAN'
            
            h, d = vals['MACD_HISTOGRAM'], vals['MACD_HISTOGRAM_DIR']
            if h is None or h == 0 or d == 0: signals['MACD_HISTOGRAM'] = 'TRUNG_TINH'
            elif h > 0 and d > 0: signals['MACD_HISTOGRAM'] = 'MUA'
            elif h < 0 and d < 0: signals['MACD_HISTOGRAM'] = 'BAN'
            else: signals['MACD_HISTOGRAM'] = 'TRUNG_TINH'
            
            v, dmp, dmn = vals['ADX'], vals.get('DMP'), vals.get('DMN')
            if v is None or v < 25: signals['ADX'] = 'TRUNG_TINH'
            elif dmp is not None and dmn is not None:
                if dmp > dmn: signals['ADX'] = 'MUA'
                elif dmn > dmp: signals['ADX'] = 'BAN'
                else: signals['ADX'] = 'TRUNG_TINH'
            else: signals['ADX'] = 'TRUNG_TINH'
            v = vals['ROC']
            if v is None: signals['ROC'] = 'TRUNG_TINH'
            elif v > 10: signals['ROC'] = 'MUA'
            elif v < -10: signals['ROC'] = 'BAN'
            else: signals['ROC'] = 'TRUNG_TINH'
            
            v = vals['SAR']
            if v is None or is_approx(price, v): signals['SAR'] = 'TRUNG_TINH'
            elif price > v: signals['SAR'] = 'MUA'
            else: signals['SAR'] = 'BAN'
            
            v = vals['BB_WIDTH']
            p_dir = vals['PRICE_DIR']
            ma20_val = mas.get('SMA_20')
            
            if v is None or ma20_val is None:
                signals['BB_WIDTH'] = 'TRUNG_TINH'
            elif v >= 0.10:
                if price < ma20_val and p_dir < 0:
                    signals['BB_WIDTH'] = 'BAN'
                elif price > ma20_val and p_dir > 0:
                    signals['BB_WIDTH'] = 'MUA'
                else:
                    signals['BB_WIDTH'] = 'TRUNG_TINH'
            else:
                signals['BB_WIDTH'] = 'TRUNG_TINH'
            
            return signals

        def classify_tab2(vals):
            signals = classify_tab1(vals) 
            def reverse_ob_os(v, ob, os):
                if v is None: return 'TRUNG_TINH'
                if v > ob: return 'BAN'
                if v < os: return 'MUA'
                return 'TRUNG_TINH'
                
            signals['RSI'] = reverse_ob_os(vals['RSI'], 70, 30)
            signals['STOCHK'] = reverse_ob_os(vals['STOCHK'], 80, 20)
            signals['STOCHRSI_FASTK'] = reverse_ob_os(vals['STOCHRSI_FASTK'], 80, 20)
            signals['WPR'] = reverse_ob_os(vals['WPR'], -20, -80)
            signals['CCI'] = reverse_ob_os(vals['CCI'], 100, -100)
            signals['ULTOSC'] = reverse_ob_os(vals['ULTOSC'], 70, 30)
            return signals

        ma_signals = {}
        for k, v in mas.items():
            if v is None: 
                ma_signals[k] = None
            elif is_approx(price, v):
                ma_signals[k] = 'TRUNG_TINH'
            elif price > v:
                ma_signals[k] = 'MUA'
            else:
                ma_signals[k] = 'BAN'

        def calc_gauge(sigs_list):
            s = sum(1 for x in sigs_list if x == 'BAN')
            n = sum(1 for x in sigs_list if x == 'TRUNG_TINH')
            b = sum(1 for x in sigs_list if x == 'MUA')
            t = s + n + b
            if t == 0:
                return {'state': 'TRUNG_TINH', 'score': 0, 'sell': 0, 'neutral': 0, 'buy': 0, 'needle_angle': 90}
            
            score = (b - s) / t
            if score >= 0.6: state = 'MUA_MANH'
            elif score >= 0.3: state = 'MUA'
            elif score > -0.3: state = 'TRUNG_TINH'
            elif score > -0.6: state = 'BAN'
            else: state = 'BAN_MANH'
            
            angle = (score + 1) / 2 * 180
            return {
                'state': state, 'score': round(score, 3), 'needle_angle': round(angle, 1),
                'sell': s, 'neutral': n, 'buy': b, 'total': t
            }

        res_data = {
            'price': price,
            'values': vals,
            'mas': mas,
            'tab1': {},
            'tab2': {}
        }
        
        t1_osc = classify_tab1(vals)
        t1_ma_vals = [v for v in ma_signals.values() if v is not None]
        t1_all = list(t1_osc.values()) + t1_ma_vals
        
        res_data['tab1'] = {
            'signals': t1_osc,
            'ma_signals': ma_signals,
            'gauge_osc': calc_gauge(list(t1_osc.values())),
            'gauge_ma': calc_gauge(t1_ma_vals),
            'gauge_overall': calc_gauge(t1_all)
        }
        
        t2_osc = classify_tab2(vals)
        t2_all = list(t2_osc.values()) + t1_ma_vals
        
        res_data['tab2'] = {
            'signals': t2_osc,
            'ma_signals': ma_signals,
            'gauge_osc': calc_gauge(list(t2_osc.values())),
            'gauge_ma': calc_gauge(t1_ma_vals),
            'gauge_overall': calc_gauge(t2_all)
        }
        
        return JSONResponse({"status": "success", "data": res_data})
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse({"status": "error", "message": str(e)})

@app.get('/api/vietcap/financial-statement')
def get_vietcap_financial_statement(symbol: str, section: str):
    endpoint = f"v1/company/{symbol}/financial-statement?section={section}"
    return fetch_vietcap_api(endpoint, symbol, f"fin_stmt_{section}")

@app.get('/api/vietcap/statistics-financial')
def get_vietcap_statistics_financial(symbol: str):
    endpoint = f"v1/company/{symbol}/statistics-financial"
    return fetch_vietcap_api(endpoint, symbol, "stat_fin")

@app.get('/api/vietcap/metrics')
def get_vietcap_metrics(symbol: str):
    return fetch_vietcap_api(f"v1/company/{symbol}/financial-statement/metrics", symbol, "fin_metrics")

@app.get('/api/vietcap/sectors/icb-codes')
def get_vietcap_icb_codes():
    import json
    import os
    try:
        with open('fialda_icb.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
            return {"data": data} if isinstance(data, list) else data
    except Exception as e:
        return {"error": str(e)}

@app.get('/api/fialda/sector-stocks/{icb_code}')
def get_fialda_sector_stocks(icb_code: str):
    import json
    try:
        with open('fialda_stock_mapping.json', 'r', encoding='utf-8') as f:
            mapping = json.load(f)
        stocks = mapping.get("sector_to_stocks", {}).get(icb_code, [])
        return {"icbCode": icb_code, "symbols": stocks}
    except Exception as e:
        return {"error": str(e)}

@app.post('/api/fialda/sync')
def sync_fialda_manual():
    import subprocess
    import os
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fialda_scraper.py')
    try:
        result = subprocess.run(['python', script_path], capture_output=True, text=True, check=True)
        return {"status": "success", "message": "Đồng bộ thành công."}
    except subprocess.CalledProcessError as e:
        return {"status": "error", "message": "Lỗi khi đồng bộ dữ liệu.", "details": e.stderr}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get('/api/vndirect/history')
def get_vndirect_history(symbol: str, resolution: str, from_ts: int, to_ts: int):
    import requests
    url = f"https://dchart-api.vndirect.com.vn/dchart/history?symbol={symbol}&resolution={resolution}&from={from_ts}&to={to_ts}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        res = requests.get(url, headers=headers)
        return res.json()
    except Exception as e:
        return {"error": str(e)}

@app.get('/api/vietcap/company/search-bar')
def get_vietcap_search_bar(keyword: str = ''):
    import requests
    url = f'https://iq.vietcap.com.vn/api/iq-insight-service/v2/company/search-bar?keyword={keyword}'
    res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
    return res.json()

@app.get('/api/vietcap/company/by-sector')
def get_vietcap_by_sector(sectorId: str):
    mapping_exact = {
        # Ngân hàng & Tài chính
        "8355": ["VCB", "BID", "CTG", "MBB", "ACB", "TCB", "VPB", "HDB", "STB", "VIB", "SHB", "EIB", "LPB", "TPB", "MSB", "OCB"],
        "8773": ["SSI", "VND", "VCI", "HCM", "SHS", "MBS", "VIX", "FTS", "BSI", "CTS", "AGR"],
        "8633": ["VHM", "VIC", "VRE", "NVL", "KDH", "NLG", "PDR", "DIG", "DXG", "CEO", "HDG", "CRE"],
        
        # Công nghệ & Hàng hóa
        "3577": ["FPT", "CMG", "ELC", "ITD", "SAM", "ICT", "SGT"],
        "3535": ["VNM", "MCH", "SAB", "MSN", "QNS", "KDC", "SBT", "DBC"],
        
        # Nguyên vật liệu
        "1353": ["GVR", "PHR", "DPR", "AAA", "DRC", "CSM"], # Nhựa, cao su & sợi
        "1357": ["DGC", "CSV", "DPM", "DCM", "BFC", "LAS"], # Hóa chất chuyên dụng (Hóa chất, phân bón)
        "1350": ["GVR", "PHR", "DPR", "AAA", "DGC", "CSV", "DPM", "DCM"], # Hóa chất nói chung
        "1755": ["HPG", "HSG", "NKG", "POM", "SMC", "TLH"], # Thép (Kim loại công nghiệp)
        "1700": ["PTB", "VCS", "HT1", "BCC"], # Xây dựng và Vật liệu
        
        # Dầu khí (Cấp 4)
        "0533": ["GAS", "PVD"], # Sản xuất và Khai thác
        "0537": ["BSR", "PLX", "OIL"], # Tổ hợp Dầu khí (Lọc hóa, Bán lẻ)
        "0573": ["PVS", "PVC", "PVT", "POS"], # Thiết bị và Dịch vụ Dầu khí
        "0577": ["GAS", "PGD", "CNG", "PVT"], # Ống dẫn dầu / Vận chuyển khí
        
        # Dầu khí (Cấp 3)
        "0530": ["GAS", "PVD", "BSR", "PLX", "OIL"], # Sản xuất Dầu khí (Tổng hợp 0533 + 0537)
        "0570": ["PVS", "PVC", "PVT", "POS"] # Phân phối Dầu khí (Tổng hợp 0573)
    }
    
    # Gom nhóm theo mã Level 1 (Ngành cấp 1) dựa trên ký tự đầu tiên
    mapping_level1 = {
        "0": ["GAS", "PVD", "PVS", "BSR", "OIL", "PLX", "PVC", "PVT", "POS"], # Dầu khí
        "1": ["HPG", "HSG", "NKG", "DPM", "DCM", "GVR", "PHR", "DPR", "PTB", "AAA", "CSV", "DGC"], # Nguyên vật liệu
        "2": ["VGC", "SZC", "KBC", "IDC", "GMD", "HAH", "MVN", "PC1", "VSH", "REE", "CII"], # Công nghiệp
        "3": ["VNM", "MSN", "SAB", "MWG", "PNJ", "FRT", "DGW", "PET", "QNS", "KDC", "TLG"], # Hàng tiêu dùng
        "4": ["DHG", "IMP", "TRA", "DMC", "AMV", "JVC", "DBD"], # Dược phẩm và Y tế
        "5": ["VJC", "HVN", "SCS", "AST", "SKG", "VTR"], # Dịch vụ tiêu dùng
        "6": ["FOC", "VGI", "CTR", "YEG", "FOX", "TTN"], # Viễn thông
        "7": ["GAS", "POW", "NT2", "REE", "PPC", "GEG", "SJD", "TDM", "BWE", "QTP", "CHP"], # Tiện ích cộng đồng
        "8": ["VCB", "BID", "CTG", "MBB", "TCB", "VPB", "ACB", "SSI", "VND", "VHM", "VIC", "VRE", "NVL"], # Tài chính
        "9": ["FPT", "CMG", "ELC", "ITD", "SAM", "ICT", "SGT"] # Công nghệ thông tin
    }
    
    # BƯỚC 1: Thử lấy dữ liệu từ file local sectors_mapping.json (nhanh 0ms, đầy đủ 100%)
    if sectorId in sectors_mapping:
        symbols = sectors_mapping[sectorId]
        if len(symbols) > 0:
            return {"data": symbols}
            
    import requests
    
    # BƯỚC 2: Thử gọi API VNDirect trực tiếp (nếu file JSON chưa có)
    try:
        url = f"https://finfo-api.vndirect.com.vn/v4/stocks?q=industryCode:{sectorId}~type:STOCK~status:LISTED&size=100"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Origin": "https://dboard.vndirect.com.vn",
            "Referer": "https://dboard.vndirect.com.vn/"
        }
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json().get('data', [])
            symbols = [item['symbol'] for item in data]
            if len(symbols) > 0:
                return {"data": symbols}
    except Exception as e:
        print(f"Lỗi kết nối VNDirect API, chuyển sang dữ liệu dự phòng: {e}")
        
    # BƯỚC 3: Cơ chế dự phòng cứng
    symbols = []
    if sectorId in mapping_exact:
        symbols = mapping_exact[sectorId]
    elif sectorId and (sectorId.endswith('00') or sectorId == '0001'):
        first_digit = sectorId[0]
        if first_digit in mapping_level1:
            symbols = mapping_level1[first_digit]
            
    return {"data": symbols}

class ExportRequest(BaseModel):
    symbol: str
    tab: str
    format: str
    columns: List[str]
    data: List[Dict[str, Any]]

@app.post('/api/vietcap/export')
async def export_vietcap_data(req: ExportRequest, background_tasks: BackgroundTasks):
    df = pd.DataFrame(req.data, columns=req.columns)
    
    ext = 'xlsx' if req.format == 'excel' else req.format
    fd, path = tempfile.mkstemp(suffix=f".{ext}")
    os.close(fd)
    
    try:
        if req.format == 'csv':
            df.to_csv(path, index=False, encoding='utf-8-sig')
        elif req.format == 'excel':
            df.to_excel(path, index=False)
        elif req.format == 'dta':
            new_cols = []
            for col in df.columns:
                clean_col = col.replace('/', '_').replace('-', '_').replace(' ', '_')
                new_cols.append(clean_col)
            df.columns = new_cols
            
            for col in df.columns:
                if col != 'Khoan_muc':
                    df[col] = pd.to_numeric(df[col], errors='coerce')
            
            df.to_stata(path, write_index=False, version=118)
            
        background_tasks.add_task(os.remove, path)
        return FileResponse(path, filename=f"{req.symbol}_{req.tab}.{ext}")
    except Exception as e:
        background_tasks.add_task(os.remove, path)
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

from apscheduler.schedulers.background import BackgroundScheduler
import subprocess

def run_fialda_scraper():
    import os
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fialda_scraper.py')
    print("[Cron] Đang chạy đồng bộ Fialda hàng tuần...")
    subprocess.Popen(['python', script_path])

@app.on_event("startup")
def setup_scheduler():
    scheduler = BackgroundScheduler()
    # Chạy vào 00:00 Chủ Nhật hàng tuần (day_of_week='sun', hour=0, minute=0)
    scheduler.add_job(run_fialda_scraper, 'cron', day_of_week='sun', hour=0, minute=0)
    scheduler.start()
    print("[Cron] Scheduler đã bắt đầu. Lịch cập nhật Fialda: 00:00 Chủ Nhật.")

app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8890, reload=True)
