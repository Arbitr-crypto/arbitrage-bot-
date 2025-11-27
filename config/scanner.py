# scanner.py
import ccxt, asyncio
from config import EXCHANGES, MIN_VOLUME_USD, MAX_COINS, SPREAD_THRESHOLD
from db import save_signal

exchanges = {}
for ex_id in EXCHANGES:
    try:
        ex_cls = getattr(ccxt, ex_id)
        exchanges[ex_id] = ex_cls({'enableRateLimit': True})
        print(f"Инициализирована биржа {ex_id}")
    except Exception as e:
        print(f"Ошибка инициализации {ex_id}: {e}")

def is_valid_symbol(symbol: str):
    if not symbol.endswith("/USDT"):
        return False
    bad_keywords = ['3S','3L','UP','DOWN','BULL','BEAR','ETF','HALF','MOON','INVERSE']
    for b in bad_keywords:
        if b in symbol.upper():
            return False
    base = symbol.split("/")[0]
    return 2 <= len(base) <= 20

async def scan_once():
    symbol_map = {}
    # Загружаем рынки
    for ex_name, ex in exchanges.items():
        try:
            markets = await asyncio.to_thread(ex.load_markets)
            usdt_pairs = [s for s in markets.keys() if is_valid_symbol(s)]
            for s in usdt_pairs:
                symbol_map.setdefault(s, []).append(ex_name)
        except Exception:
            continue

    common_symbols = [s for s, exs in symbol_map.items() if len(exs) >= 2][:MAX_COINS]

    for symbol in common_symbols:
        ex_list = symbol_map[symbol]
        for buy_ex in ex_list:
            for sell_ex in ex_list:
                if buy_ex == sell_ex:
                    continue
                try:
                    ob_buy = await asyncio.to_thread(exchanges[buy_ex].fetch_order_book, symbol, 5)
                    ob_sell = await asyncio.to_thread(exchanges[sell_ex].fetch_order_book, symbol, 5)
                except Exception:
                    continue
                if not ob_buy.get('asks') or not ob_sell.get('bids'):
                    continue
                ask_price, ask_amt = ob_buy['asks'][0]
                bid_price, bid_amt = ob_sell['bids'][0]
                if ask_price <= 0:
                    continue
                spread = (bid_price - ask_price) / ask_price
                volume = max(sum([p*a for p,a in ob_buy['bids'][:3]]),
                             sum([p*a for p,a in ob_sell['asks'][:3]]))
                if spread < SPREAD_THRESHOLD or volume < MIN_VOLUME_USD:
                    continue
                save_signal(symbol, buy_ex, sell_ex, spread, volume)
