import asyncio
from fastapi import WebSocket
from typing import Dict, List
import json
import threading

class OrderBookManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}
        self.loop = None
        
        # Start real data loop using VNDirect + VPS fallback
        self.real_thread = threading.Thread(target=self._real_data_loop, daemon=True)
        self.real_thread.start()

    async def connect(self, websocket: WebSocket, symbol: str):
        if self.loop is None:
            self.loop = asyncio.get_running_loop()
        await websocket.accept()
        if symbol not in self.active_connections:
            self.active_connections[symbol] = []
        self.active_connections[symbol].append(websocket)

    def disconnect(self, websocket: WebSocket, symbol: str):
        if symbol in self.active_connections:
            if websocket in self.active_connections[symbol]:
                self.active_connections[symbol].remove(websocket)

    def broadcast(self, symbol: str, message: dict):
        if not self.loop:
            return
        if symbol in self.active_connections and self.active_connections[symbol]:
            for connection in list(self.active_connections[symbol]):
                try:
                    asyncio.run_coroutine_threadsafe(connection.send_json(message), self.loop)
                except Exception as e:
                    print("Error broadcasting message:", e)

    def _real_data_loop(self):
        import time
        import requests
        from datetime import datetime
        
        # Track state per symbol for detecting new matches
        last_state = {}
        
        while True:
            time.sleep(2)
            
            for symbol in list(self.active_connections.keys()):
                if not self.active_connections[symbol]:
                    continue
                
                try:
                    headers = {'User-Agent': 'Mozilla/5.0'}
                    
                    # ── PRIMARY: VPS API (fast, live bid/ask data) ──────────────
                    vps_ok = False
                    try:
                        r_vps = requests.get(
                            f'https://bgapidatafeed.vps.com.vn/getliststockdata/{symbol}',
                            headers=headers, timeout=4
                        )
                        if r_vps.status_code == 200:
                            vps_data = r_vps.json()
                            if vps_data and len(vps_data) > 0:
                                item = vps_data[0]
                                
                                def parse_g(g_str):
                                    if not g_str: return 0.0, 0
                                    parts = g_str.split('|')
                                    if len(parts) >= 2:
                                        try:
                                            price = float(parts[0])
                                            vol = int(parts[1]) * 10
                                            return price, vol
                                        except: pass
                                    return 0.0, 0

                                bp1, bv1 = parse_g(item.get('g1'))
                                bp2, bv2 = parse_g(item.get('g2'))
                                bp3, bv3 = parse_g(item.get('g3'))
                                ap1, av1 = parse_g(item.get('g4'))
                                ap2, av2 = parse_g(item.get('g5'))
                                ap3, av3 = parse_g(item.get('g6'))
                                
                                ref_price = float(item.get('r', 0) or 0)
                                last_price = float(item.get('lastPrice', 0) or 0)
                                total_lot = int(item.get('lot', 0) or 0)
                                total_vol = total_lot * 10

                                msg = {
                                    "DataType": "X",
                                    "Symbol": symbol,
                                    "BidPrice1": bp1, "BidVol1": bv1,
                                    "BidPrice2": bp2, "BidVol2": bv2,
                                    "BidPrice3": bp3, "BidVol3": bv3,
                                    "AskPrice1": ap1, "AskVol1": av1,
                                    "AskPrice2": ap2, "AskVol2": av2,
                                    "AskPrice3": ap3, "AskVol3": av3,
                                    "RefPrice": ref_price,
                                    "CeilPrice": float(item.get('c', 0) or 0),
                                    "FloorPrice": float(item.get('f', 0) or 0),
                                    "HighPrice": float(item.get('highPrice', 0) or 0),
                                    "LowPrice": float(item.get('lowPrice', 0) or 0),
                                    "OpenPrice": float(item.get('openPrice', 0) or 0),
                                    "FBVol": float(item.get('fBVol', 0) or 0),
                                    "FSVol": float(item.get('fSVolume', 0) or 0),
                                    "FBVal": float(item.get('fBValue', 0) or 0),
                                    "FSVal": float(item.get('fSValue', 0) or 0),
                                    "TotalVol": total_vol,
                                    "Source": "VPS"
                                }
                                self.broadcast(symbol, msg)
                                vps_ok = True

                                # Detect new match from volume change
                                prev_lot = last_state.get(symbol, {}).get('lot', total_lot)
                                if total_lot > prev_lot:
                                    match_vol = (total_lot - prev_lot) * 10
                                    
                                    # Determine buy/sell direction
                                    # If last price >= best ask → buy-initiated
                                    # If last price <= best bid → sell-initiated
                                    if ap1 > 0 and last_price >= ap1:
                                        action = "B"
                                    elif bp1 > 0 and last_price <= bp1:
                                        action = "S"
                                    else:
                                        # Fallback: compare to ref
                                        action = "B" if last_price > ref_price else "S"

                                    color_class = "up" if last_price > ref_price else ("down" if last_price < ref_price else "ref")
                                    
                                    s_msg = {
                                        "DataType": "S",
                                        "Symbol": symbol,
                                        "Price": last_price,
                                        "Vol": match_vol,
                                        "Time": datetime.now().strftime("%H:%M:%S"),
                                        "Action": action,
                                        "Change": color_class
                                    }
                                    self.broadcast(symbol, s_msg)
                                
                                if symbol not in last_state:
                                    last_state[symbol] = {}
                                last_state[symbol]['lot'] = total_lot
                                last_state[symbol]['last_price'] = last_price

                    except Exception as vps_err:
                        print(f"VPS fetch error for {symbol}: {vps_err}")

                    # ── FALLBACK: CafeF if VPS failed ────────
                    if not vps_ok:
                        try:
                            item = None
                            for center in [1, 2, 9]:
                                r_cafef = requests.get(f'https://banggia.cafef.vn/stockhandler.ashx?center={center}', headers=headers, timeout=5)
                                if r_cafef.status_code == 200:
                                    for row in r_cafef.json():
                                        if row.get('a') == symbol.upper():
                                            item = row
                                            break
                                if item: break
                            
                            if item:
                                bp1 = float(item.get('e') or 0)
                                bv1 = int(item.get('f') or 0) * 10
                                bp2 = float(item.get('g') or 0)
                                bv2 = int(item.get('h') or 0) * 10
                                bp3 = float(item.get('i') or 0)
                                bv3 = int(item.get('j') or 0) * 10
                                
                                ap1 = float(item.get('s') or 0)
                                av1 = int(item.get('t') or 0) * 10
                                ap2 = float(item.get('q') or 0)
                                av2 = int(item.get('r') or 0) * 10
                                ap3 = float(item.get('o') or 0)
                                av3 = int(item.get('p') or 0) * 10
                                
                                ref_price = float(item.get('b') or 0)
                                last_price = float(item.get('l') or 0)
                                total_vol = int(item.get('n') or 0) * 10
                                
                                msg = {
                                    "DataType": "X",
                                    "Symbol": symbol,
                                    "BidPrice1": bp1, "BidVol1": bv1,
                                    "BidPrice2": bp2, "BidVol2": bv2,
                                    "BidPrice3": bp3, "BidVol3": bv3,
                                    "AskPrice1": ap1, "AskVol1": av1,
                                    "AskPrice2": ap2, "AskVol2": av2,
                                    "AskPrice3": ap3, "AskVol3": av3,
                                    "RefPrice": ref_price,
                                    "CeilPrice": float(item.get('c') or 0),
                                    "FloorPrice": float(item.get('d') or 0),
                                    "HighPrice": float(item.get('v') or 0),
                                    "LowPrice": float(item.get('w') or 0),
                                    "OpenPrice": 0,
                                    "FBVol": float(item.get('x') or 0) * 10,
                                    "FSVol": float(item.get('y') or 0) * 10,
                                    "FBVal": 0, "FSVal": 0,
                                    "TotalVol": total_vol,
                                    "Source": "CafeF"
                                }
                                self.broadcast(symbol, msg)
                                
                                prev_vol = last_state.get(symbol, {}).get('vol_sum', total_vol)
                                if total_vol > prev_vol:
                                    delta = total_vol - prev_vol
                                    action = "B"
                                    if ap1 > 0 and last_price >= ap1: action = "B"
                                    elif bp1 > 0 and last_price <= bp1: action = "S"
                                    else: action = "B" if last_price > ref_price else "S"
                                    
                                    color_class = "up" if last_price > ref_price else ("down" if last_price < ref_price else "ref")
                                    s_msg = {
                                        "DataType": "S",
                                        "Symbol": symbol,
                                        "Price": last_price,
                                        "Vol": delta,
                                        "Time": datetime.now().strftime("%H:%M:%S"),
                                        "Action": action,
                                        "Change": color_class
                                    }
                                    self.broadcast(symbol, s_msg)
                                
                                if symbol not in last_state:
                                    last_state[symbol] = {}
                                last_state[symbol]['vol_sum'] = total_vol
                                last_state[symbol]['last_price'] = last_price
                        except Exception as cafef_err:
                            print(f"CafeF fallback error for {symbol}: {cafef_err}")
                            
                except Exception as e:
                    print(f"Error in data loop for {symbol}:", e)

orderbook_manager = OrderBookManager()
