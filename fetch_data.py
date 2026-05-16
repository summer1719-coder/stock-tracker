"""
台股追蹤板 - 自動抓資料腳本
每天收盤後由 GitHub Actions 執行
抓取：台股收盤價、美股指數、外資買賣超
結果存成 data.json，供網頁讀取
"""

import json
import datetime
import requests
import time
import os

# ── 你的追蹤清單（可以自己改）──────────────────────────────
STOCK_CODES = ['2330', '0050', '0056', '00878', '00940', '3006', '4533']

# ── 工具函式 ────────────────────────────────────────────────

def log(msg):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}")

def safe_float(val, default=0):
    try:
        return float(str(val).replace(',', '').replace('%', ''))
    except:
        return default

# ── 1. 抓台股收盤價（證交所官方 API，免費，無需 key）──────────

def fetch_tw_stocks(codes):
    """
    使用台灣證交所 + 櫃買中心 API
    回傳格式: { '2330': { name, price, change, changePct, vol, prev }, ... }
    """
    result = {}
    today = datetime.datetime.now().strftime('%Y%m%d')

    # 先抓上市（TSE）
    try:
        log("抓取上市股票（TWSE）...")
        url = f"https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL?response=json&date={today}"
        r = requests.get(url, timeout=15, headers={'User-Agent': 'Mozilla/5.0'})
        data = r.json()
        if data.get('stat') == 'OK':
            for row in data.get('data', []):
                code = row[0].strip()
                if code in codes:
                    try:
                        prev  = safe_float(row[3])   # 開盤
                        close = safe_float(row[6])   # 收盤
                        change = round(close - prev, 2)
                        changePct = round((change / prev * 100), 2) if prev else 0
                        vol = row[2]                 # 成交張數
                        result[code] = {
                            'name'      : row[1].strip(),
                            'price'     : close,
                            'prev'      : prev,
                            'change'    : change,
                            'changePct' : changePct,
                            'vol'       : vol + '張',
                            'source'    : 'TWSE'
                        }
                    except Exception as e:
                        log(f"  解析 {code} 失敗: {e}")
        log(f"  上市取得 {len(result)} 筆")
    except Exception as e:
        log(f"  上市 API 失敗: {e}")

    # 補抓上櫃（OTC）：0056、00878、00940 等 ETF 在這
    otc_codes = [c for c in codes if c not in result]
    if otc_codes:
        try:
            log("抓取上櫃股票（OTC）...")
            url2 = f"https://www.tpex.org.tw/web/stock/aftertrading/otc_quotes_no1430/stk_wn1430_result.php?l=zh-tw&d={datetime.datetime.now().strftime('%Y/%m/%d')}&se=EW"
            r2 = requests.get(url2, timeout=15, headers={'User-Agent': 'Mozilla/5.0'})
            data2 = r2.json()
            for row in data2.get('aaData', []):
                code = row[0].strip()
                if code in otc_codes:
                    try:
                        close  = safe_float(row[2])
                        change = safe_float(row[3])
                        prev   = round(close - change, 2)
                        changePct = round((change / prev * 100), 2) if prev else 0
                        result[code] = {
                            'name'      : row[1].strip(),
                            'price'     : close,
                            'prev'      : prev,
                            'change'    : change,
                            'changePct' : changePct,
                            'vol'       : row[7] + '張',
                            'source'    : 'OTC'
                        }
                    except Exception as e:
                        log(f"  解析 {code} 失敗: {e}")
            log(f"  上櫃補抓完，總計 {len(result)} 筆")
        except Exception as e:
            log(f"  上櫃 API 失敗: {e}")

    return result

# ── 2. 抓美股指數（Yahoo Finance，免費）──────────────────────

def fetch_us_markets():
    """
    抓取：費半SOX、Nasdaq、S&P500、輝達NVDA、美元DXY、10年期公債
    """
    symbols = {
        'SOX'   : '^SOX',
        'NASDAQ': '^IXIC',
        'SP500' : '^GSPC',
        'NVDA'  : 'NVDA',
        'DXY'   : 'DX-Y.NYB',
        'US10Y' : '^TNX',
    }
    result = {}
    for key, sym in symbols.items():
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=2d"
            r = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
            d = r.json()
            closes = d['chart']['result'][0]['indicators']['quote'][0]['close']
            closes = [c for c in closes if c is not None]
            if len(closes) >= 2:
                prev  = round(closes[-2], 2)
                close = round(closes[-1], 2)
                change = round(close - prev, 2)
                changePct = round((change / prev * 100), 2) if prev else 0
                result[key] = {
                    'price'     : close,
                    'prev'      : prev,
                    'change'    : change,
                    'changePct' : changePct,
                }
            time.sleep(0.3)  # 避免太快被擋
        except Exception as e:
            log(f"  {key} ({sym}) 失敗: {e}")
    log(f"美股取得 {len(result)} 筆")
    return result

# ── 3. 抓外資買賣超（證交所）────────────────────────────────

def fetch_foreign_buy():
    try:
        log("抓取外資買賣超...")
        url = "https://www.twse.com.tw/fund/TWT38U?response=json&selectType=ALLBUT0999"
        r = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
        data = r.json()
        rows = data.get('data', [])
        if rows:
            # 最後一行通常是合計
            last = rows[-1]
            buy  = safe_float(last[4].replace(',', ''))  # 買進金額（千元）
            sell = safe_float(last[5].replace(',', ''))
            net  = round((buy - sell) / 100000000, 1)    # 轉換為億
            return net
    except Exception as e:
        log(f"  外資買賣超失敗: {e}")
    return None

# ── 4. 抓台幣匯率 ────────────────────────────────────────────

def fetch_usd_twd():
    try:
        url = "https://tw.rter.info/capi.php"
        r = requests.get(url, timeout=8)
        data = r.json()
        rate = data.get('USDTWD', {}).get('Exrate')
        if rate:
            return round(float(rate), 2)
    except Exception as e:
        log(f"  匯率失敗: {e}")
    return None

# ── 5. 加入本益比（靜態補充，因為即時PE需要付費API）──────────

PE_TABLE = {
    '2330': None,   # 由Yahoo補
    '0050': None,
    '0056': None,
    '00878': None,
    '00940': None,
    '3006': None,
    '4533': None,
}

def enrich_pe(stocks_data):
    """用 Yahoo Finance 補本益比"""
    for code, info in stocks_data.items():
        try:
            sym = f"{code}.TW"
            url = f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{sym}?modules=defaultKeyStatistics"
            r = requests.get(url, timeout=8, headers={'User-Agent': 'Mozilla/5.0'})
            d = r.json()
            pe = d['quoteSummary']['result'][0]['defaultKeyStatistics'].get('forwardPE', {}).get('raw')
            if pe:
                stocks_data[code]['pe'] = round(pe, 1)
            time.sleep(0.3)
        except:
            pass
    return stocks_data

# ── 主程式 ───────────────────────────────────────────────────

def main():
    log("=== 開始抓資料 ===")
    now = datetime.datetime.now()

    # 抓資料
    tw_stocks  = fetch_tw_stocks(STOCK_CODES)
    tw_stocks  = enrich_pe(tw_stocks)
    us_markets = fetch_us_markets()
    foreign    = fetch_foreign_buy()
    usd_twd    = fetch_usd_twd()

    # 組合輸出
    output = {
        "updatedAt" : now.strftime('%Y/%m/%d %H:%M'),
        "tradingDate": now.strftime('%Y/%m/%d'),
        "stocks"    : tw_stocks,
        "usMarkets" : us_markets,
        "twMarket"  : {
            "foreignBuy": foreign,   # 億，正數=買超
            "usdTwd"    : usd_twd,
        }
    }

    # 存檔
    out_path = os.path.join(os.path.dirname(__file__), 'data.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    log(f"=== 完成！寫入 data.json，共 {len(tw_stocks)} 檔台股、{len(us_markets)} 項美股指標 ===")

if __name__ == '__main__':
    main()
