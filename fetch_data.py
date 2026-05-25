"""
台股追蹤板 - 自動抓資料腳本 v9 (修正時區與強制收盤判斷)
"""

import json, datetime, requests, time, os, re
import yfinance as yf

# ── 預設追蹤清單 ──────────────────
STOCK_CODES      = ['2330', '0050', '0056', '00878', '00940', '3006', '4533']
MAX_NEWS         = 3

def log(msg):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}")

# 🌟 自動讀取網頁端新增的標的 🌟
USER_DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'userdata.json')
if os.path.exists(USER_DATA_PATH):
    try:
        with open(USER_DATA_PATH, 'r', encoding='utf-8') as f:
            user_data = json.load(f)
            if 'stocks' in user_data:
                user_codes = [s['code'] for s in user_data['stocks'] if 'code' in s]
                STOCK_CODES = list(set(STOCK_CODES + user_codes))
                log(f"✅ 成功載入雲端名單，目前總共追蹤 {len(STOCK_CODES)} 檔標的！")
    except Exception as e:
        log(f"讀取 userdata.json 失敗: {e}")

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8',
}

def safe_float(val, default=0):
    try:
        return float(str(val).replace(',','').replace('%','').strip())
    except:
        return default

def tw_now():
    # 強制將伺服器時間轉換為台灣時間 (UTC+8)
    return datetime.datetime.utcnow() + datetime.timedelta(hours=8)

def detect_mode():
    forced = os.environ.get('MODE', '').strip().lower()
    if forced in ('premarket', 'close'):
        log(f"強制模式：{forced}")
        return forced
    
    # 🌟 關鍵修改：用台灣時間來判斷，下午 2 點 (14) 以前算早班，以後算晚班
    hour = tw_now().hour
    mode = 'premarket' if hour < 14 else 'close'
    log(f"目前台灣時間 {tw_now().strftime('%Y/%m/%d %H:%M:%S')} → 判定模式為：{mode}")
    return mode

# ════════════════════════════════════════════════════
# 美股指數（包含 VIX 與 GOLD）
# ════════════════════════════════════════════════════

def fetch_us_markets():
    symbols = {
        'SOX'   : '^SOX',
        'NASDAQ': '^IXIC',
        'SP500' : '^GSPC',
        'NVDA'  : 'NVDA',
        'DXY'   : 'DX-Y.NYB',
        'US10Y' : '^TNX',
        'VIX'   : '^VIX',   
        'GOLD'  : 'GC=F',   
    }
    result = {}
    log("使用 yfinance 抓取美股指數...")
    
    for key, sym in symbols.items():
        try:
            ticker = yf.Ticker(sym)
            hist = ticker.history(period="5d")
            
            if not hist.empty and len(hist) >= 2:
                closes = hist['Close'].tolist()
                dates = hist.index.strftime('%Y/%m/%d').tolist()
                
                prev  = round(closes[-2], 2)
                close = round(closes[-1], 2)
                chg   = round(close - prev, 2)
                pct   = round((chg / prev * 100), 2) if prev else 0
                last_trade_date = dates[-1]
                
                result[key] = {
                    'price': close, 'prev': prev, 'change': chg, 
                    'changePct': pct, 'last_trade': last_trade_date
                }
                log(f"  {key}: {close} ({'+' if pct>=0 else ''}{pct}%)")
        except Exception as e:
            log(f"  {key} 失敗: {e}")
    return result

def fetch_usd_twd():
    try:
        r = requests.get("https://tw.rter.info/capi.php", timeout=8, headers=HEADERS)
        rate = r.json().get('USDTWD', {}).get('Exrate')
        if rate: return round(float(rate), 2)
    except: pass
    return None

# ════════════════════════════════════════════════════
# 開盤前任務：美股分析 + AI 預測
# ════════════════════════════════════════════════════

def build_premarket_summary(us, usd_twd):
    sox = us.get('SOX', {}); nvda = us.get('NVDA', {}); dxy = us.get('DXY', {}); us10y = us.get('US10Y', {})
    vix = us.get('VIX', {}); gold = us.get('GOLD', {})
    
    score = 0
    if sox.get('changePct', 0) > 1: score += 2
    elif sox.get('changePct', 0) > 0: score += 1
    elif sox.get('changePct', 0) < -1: score -= 2
    else: score -= 1

    if nvda.get('changePct', 0) > 2: score += 1
    elif nvda.get('changePct', 0) < -2: score -= 1
    if dxy.get('changePct', 0) < -0.3: score += 1
    elif dxy.get('changePct', 0) > 0.3: score -= 1
    if us10y.get('price', 4.5) < 4.0: score += 1
    elif us10y.get('price', 4.5) > 4.8: score -= 1
    
    if vix.get('price', 15) > 30: score -= 2
    elif vix.get('price', 15) > 20: score -= 1
    elif vix.get('price', 15) < 15: score += 1

    if score >= 3:
        outlook, summary = "🟢 偏多", "美股昨晚表現強勁，市場情緒穩定，台股今日開盤預計跟漲，科技股尤其值得關注。"
    elif score >= 1:
        outlook, summary = "🟢 小幅偏多", "美股昨晚小漲，台股今日開盤預計溫和上漲，整體氣氛尚可。"
    elif score == 0:
        outlook, summary = "🟡 中性", "美股昨晚漲跌互見，台股今日開盤方向不明，建議觀望為主。"
    elif score >= -2:
        outlook, summary = "🔴 小幅偏空", "美股昨晚偏弱，台股今日開盤預計承壓，注意避險情緒。"
    else:
        outlook, summary = "🔴 偏空", "美股昨晚明顯下跌且恐慌情緒升溫，台股今日開盤預計跟跌，建議謹慎。"

    details = []
    details.append(f"費半 {sox.get('changePct', 0)}%（{'正面' if sox.get('changePct', 0)>0 else '負面'}訊號，直接影響台積電等半導體股）")
    details.append(f"輝達 {nvda.get('changePct', 0)}%（{'AI概念股跟漲' if nvda.get('changePct', 0)>0 else 'AI族群可能承壓'}）")
    details.append(f"美國10年期公債 {us10y.get('price', 0)}%（資金流向參考）")
    details.append(f"VIX 恐慌指數 {vix.get('price', 0)}（{'大於20，市場情緒緊張' if vix.get('price', 0) > 20 else '小於20，市場情緒尚屬穩定'}）")
    details.append(f"黃金價格 {gold.get('price', 0)}（避險情緒指標）")
    
    return {'outlook': outlook, 'summary': summary, 'details': details, 'score': score}

def run_premarket():
    log("=== 開盤前任務開始 ===")
    us = fetch_us_markets()
    usd_twd = fetch_usd_twd()
    preview = build_premarket_summary(us, usd_twd)

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data.json')
    existing = {}
    if os.path.exists(out_path):
        try:
            with open(out_path, 'r', encoding='utf-8') as f: existing = json.load(f)
        except: pass

    existing.update({
        "mode": "premarket",
        "premarketAt": tw_now().strftime('%Y/%m/%d %H:%M'),
        "usMarkets": us,
        "twMarket": { **existing.get('twMarket', {}), "usdTwd": usd_twd },
        "openingPreview": preview,
    })

    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    log("=== 開盤前任務完成 ===")

# ════════════════════════════════════════════════════
# 收盤後任務：台股收盤價 + 新聞 
# ════════════════════════════════════════════════════

def fetch_tw_stocks(codes):
    result = {}
    today  = tw_now().strftime('%Y%m%d')

    try:
        log("抓取上市股票（TWSE）...")
        url  = f"https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL?response=json&date={today}"
        r    = requests.get(url, timeout=15, headers=HEADERS)
        data = r.json()
        if data.get('stat') == 'OK':
            for row in data.get('data', []):
                code = row[0].strip()
                if code in codes:
                    try:
                        close = safe_float(row[7])
                        chg   = safe_float(str(row[8]).replace('X', '').replace('+', ''))
                        prev  = round(close - chg, 2)
                        pct   = round((chg / prev * 100), 2) if prev else 0
                        vol_sheets = int(safe_float(row[2]) / 1000)
                        
                        result[code] = {
                            'name': row[1].strip(), 'price': close, 'prev': prev,
                            'high': safe_float(row[5]), 'low': safe_float(row[6]),
                            'change': chg, 'changePct': pct,
                            'vol': f"{vol_sheets:,}張", 'source': 'TWSE'
                        }
                    except Exception as e:
                        log(f"  解析 {code} 失敗: {e}")
        log(f"  上市取得 {len(result)} 筆")
    except Exception as e:
        log(f"  上市 API 失敗: {e}")

    otc_codes = [c for c in codes if c not in result]
    if otc_codes:
        try:
            log("抓取上櫃股票（OTC）...")
            d_str = tw_now().strftime('%Y/%m/%d')
            url2  = (f"https://www.tpex.org.tw/web/stock/aftertrading/otc_quotes_no1430/stk_wn1430_result.php?l=zh-tw&d={d_str}&se=EW")
            r2    = requests.get(url2, timeout=15, headers=HEADERS)
            for row in r2.json().get('aaData', []):
                code = row[0].strip()
                if code in otc_codes:
                    try:
                        close = safe_float(row[2])
                        chg   = safe_float(str(row[3]).replace('X', '').replace('+', ''))
                        prev  = round(close - chg, 2)
                        pct   = round((chg / prev * 100), 2) if prev else 0
                        vol_sheets = int(safe_float(row[7]) / 1000)
                        
                        result[code] = {
                            'name': row[1].strip(), 'price': close, 'prev': prev,
                            'high': safe_float(row[5]), 'low': safe_float(row[6]),
                            'change': chg, 'changePct': pct,
                            'vol': f"{vol_sheets:,}張", 'source': 'OTC'
                        }
                    except Exception as e:
                        log(f"  解析 {code} 失敗: {e}")
            log(f"  上櫃補抓完，總計 {len(result)} 筆")
        except Exception as e:
            log(f"  上櫃 API 失敗: {e}")

    return result

def enrich_pe(stocks_data):
    log("補抓本益比、殖利率...")
    for code in list(stocks_data.keys()):
        try:
            sym = f"{code}.TW"
            url = (f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{sym}?modules=defaultKeyStatistics,summaryDetail")
            r   = requests.get(url, timeout=8, headers=HEADERS)
            res = r.json()['quoteSummary']['result'][0]
            pe  = (res.get('summaryDetail',{}).get('trailingPE',{}).get('raw')
                   or res.get('defaultKeyStatistics',{}).get('forwardPE',{}).get('raw'))
            if pe: stocks_data[code]['pe'] = round(pe, 1)
            dy = res.get('summaryDetail',{}).get('dividendYield',{}).get('raw')
            if dy: stocks_data[code]['dividendYield'] = round(dy * 100, 2)
            time.sleep(0.4)
        except: pass
    return stocks_data

def fetch_foreign_buy():
    try:
        r = requests.get("https://www.twse.com.tw/fund/TWT38U?response=json&selectType=ALLBUT0999", timeout=10, headers=HEADERS)
        rows = r.json().get('data', [])
        if rows:
            buy  = safe_float(str(rows[-1][4]).replace(',',''))
            sell = safe_float(str(rows[-1][5]).replace(',',''))
            return round((buy - sell) / 100000000, 1)
    except: pass
    return None

def fetch_yahoo_news(code):
    news = []
    try:
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={code}.TW&newsCount=5&lang=zh-TW"
        r = requests.get(url, timeout=10, headers=HEADERS)
        for item in r.json().get('news', [])[:MAX_NEWS]:
            title = item.get('title','').strip()
            pt = item.get('providerPublishTime', 0)
            ds = datetime.datetime.fromtimestamp(pt).strftime('%Y/%m/%d') if pt else ''
            if title: news.append(f"{title}（{ds}）" if ds else title)
        time.sleep(0.3)
    except: pass
    return news

def fetch_mops_news(code):
    news = []
    try:
        now = datetime.datetime.now()
        d1  = (now - datetime.timedelta(days=60)).strftime('%Y%m%d')
        d2  = now.strftime('%Y%m%d')
        url = (f"https://mops.twse.com.tw/mops/web/ajax_t05st01?encodeURIComponent=1&step=1&firstin=1&off=1&keyword4=&code1=&TYPEK=all&co_id={code}&date1={d1}&date2={d2}")
        r   = requests.post(url, timeout=12, headers={**HEADERS, 'Referer':'https://mops.twse.com.tw/'})
        count = 0
        for row in re.findall(r'<tr[^>]*>(.*?)</tr>', r.text, re.DOTALL):
            cells = [re.sub(r'<[^>]+>','',c).strip() for c in re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)]
            cells = [c for c in cells if c]
            if len(cells) >= 3:
                dp = cells[0].strip(); tp = cells[2].strip()
                if any(k in dp for k in ['發言','日期','序號']): continue
                if dp and tp and len(tp) > 4:
                    news.append(f"{tp}（{dp}）")
                    count += 1
                    if count >= MAX_NEWS: break
    except: pass
    return news

def fetch_all_news(codes):
    all_news = {}
    log("抓取個股新聞...")
    for code in codes:
        news = []
        for fetcher in [fetch_yahoo_news, fetch_mops_news]:
            if len(news) >= MAX_NEWS: break
            for item in fetcher(code):
                if item not in news: news.append(item)
                if len(news) >= MAX_NEWS: break
        all_news[code] = news[:MAX_NEWS]
        time.sleep(0.5)
    return all_news

def fetch_market_news():
    news = []
    try:
        url = "https://query2.finance.yahoo.com/v1/finance/search?q=台股&newsCount=5&lang=zh-TW"
        r = requests.get(url, timeout=10, headers=HEADERS)
        for item in r.json().get('news', [])[:5]:
            title = item.get('title','').strip()
            pt = item.get('providerPublishTime', 0)
            ds = datetime.datetime.fromtimestamp(pt).strftime('%Y/%m/%d') if pt else ''
            if title: news.append({'title': title, 'date': ds, 'url': item.get('link','')})
    except: pass
    return news

def run_close():
    log("=== 收盤後任務開始 ===")
    tw_stocks   = fetch_tw_stocks(STOCK_CODES)
    tw_stocks   = enrich_pe(tw_stocks)
    us_markets  = fetch_us_markets()
    foreign     = fetch_foreign_buy()
    usd_twd     = fetch_usd_twd()
    stock_news  = fetch_all_news(STOCK_CODES)
    market_news = fetch_market_news()

    now = tw_now()
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data.json')
    existing = {}
    if os.path.exists(out_path):
        try:
            with open(out_path, 'r', encoding='utf-8') as f: existing = json.load(f)
        except: pass

    output = {
        "mode"       : "close",
        "updatedAt"  : now.strftime('%Y/%m/%d %H:%M'),
        "tradingDate": now.strftime('%Y/%m/%d'),
        "stocks"     : tw_stocks,
        "usMarkets"  : us_markets,
        "twMarket"   : {
            "foreignBuy" : foreign,
            "usdTwd"     : usd_twd,
            "marketNews" : market_news,
        },
        "stockNews"  : stock_news,
        "openingPreview": existing.get("openingPreview"),
        "premarketAt": existing.get("premarketAt")
    }

    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    total_news = sum(len(v) for v in stock_news.values())
    log(f"=== 收盤後任務完成！台股 {len(tw_stocks)} 檔、新聞 {total_news} 則 ===")

# ── 主程式 ──────────────────────────────────────────────────────

def main():
    mode = detect_mode()
    
    if mode == 'premarket':
        run_premarket()
    else:
        run_close()

if __name__ == '__main__':
    main()
