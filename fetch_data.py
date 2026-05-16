"""
台股追蹤板 - 自動抓資料腳本 v3 (升級週末防呆版)
兩個執行時間點，任務不同：

  早上 08:45（開盤前）
    → 抓美股昨晚收盤（費半、Nasdaq、NVDA、美元、公債）
    → 抓台幣匯率
    → 產生 AI 開盤預測文字
    → mode = "premarket"

  下午 15:10（收盤後）
    → 抓台股收盤價、本益比、殖利率
    → 抓外資買賣超
    → 抓個股新聞
    → mode = "close"

結果合併寫入 data.json，網頁依 mode 決定顯示什麼
"""

import json, datetime, requests, time, os, re

# ── 你的追蹤清單 ────────────────────────────────────────────────
STOCK_CODES      = ['2330', '0050', '0056', '00878', '00940', '3006', '4533']
MAX_NEWS         = 3

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8',
}

def log(msg):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}")

def safe_float(val, default=0):
    try:
        return float(str(val).replace(',','').replace('%','').strip())
    except:
        return default

def tw_now():
    """取得台灣時間"""
    return datetime.datetime.utcnow() + datetime.timedelta(hours=8)

def detect_mode():
    """
    依台灣時間判斷要跑哪個模式
      00:00–12:00 → premarket（早上任務）
      12:00–24:00 → close（收盤任務）
    也可以用環境變數 MODE=premarket / close 強制指定
    """
    forced = os.environ.get('MODE', '').strip().lower()
    if forced in ('premarket', 'close'):
        log(f"強制模式：{forced}")
        return forced
    hour = tw_now().hour
    mode = 'premarket' if hour < 12 else 'close'
    log(f"台灣時間 {hour:02d} 點 → 模式：{mode}")
    return mode

# ════════════════════════════════════════════════════
# 美股指數（兩個模式都會用到） - 已升級週末防呆機制
# ════════════════════════════════════════════════════

def fetch_us_markets():
    symbols = {
        'SOX'   : '^SOX',
        'NASDAQ': '^IXIC',
        'SP500' : '^GSPC',
        'NVDA'  : 'NVDA',
        'DXY'   : 'DX-Y.NYB',
        'US10Y' : '^TNX',
    }
    result = {}
    log("抓取美股指數...")
    
    # 遇到週末或週一早上，我們拉長查詢區間到 5d，確保能抓到上週五的真實收盤資料
    range_str = "5d" 

    for key, sym in symbols.items():
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range={range_str}"
            r   = requests.get(url, timeout=10, headers=HEADERS)
            d   = r.json()
            
            timestamps = d['chart']['result'][0]['timestamp']
            closes = d['chart']['result'][0]['indicators']['quote'][0]['close']
            
            # 過濾掉 None 的資料，並把 timestamp 和 close 綁在一起
            valid_data = [(ts, c) for ts, c in zip(timestamps, closes) if c is not None]
            
            if len(valid_data) >= 2:
                # 永遠取最後兩筆有效的收盤價
                prev  = round(valid_data[-2][1], 2)
                close = round(valid_data[-1][1], 2)
                chg   = round(close - prev, 2)
                pct   = round((chg / prev * 100), 2) if prev else 0
                
                # 轉換最後一筆資料的時間，確認是哪一天的收盤
                last_trade_date = datetime.datetime.fromtimestamp(valid_data[-1][0]).strftime('%Y/%m/%d')
                
                result[key] = {
                    'price': close, 
                    'prev': prev, 
                    'change': chg, 
                    'changePct': pct,
                    'last_trade': last_trade_date
                }
                log(f"  {key}: {close} ({'+' if pct>=0 else ''}{pct}%) - 最後交易日: {last_trade_date}")
            time.sleep(0.4)
        except Exception as e:
            log(f"  {key} 失敗: {e}")
            
    return result

def fetch_usd_twd():
    try:
        r    = requests.get("https://tw.rter.info/capi.php", timeout=8, headers=HEADERS)
        rate = r.json().get('USDTWD', {}).get('Exrate')
        if rate:
            val = round(float(rate), 2)
            log(f"  匯率: {val}")
            return val
    except Exception as e:
        log(f"  匯率失敗: {e}")
    return None

# ════════════════════════════════════════════════════
# 開盤前任務：美股分析 + AI 預測
# ════════════════════════════════════════════════════

def build_premarket_summary(us, usd_twd):
    """
    根據美股數字產生一段白話開盤預測
    不需要 Claude API，純用規則判斷
    這樣不需要額外費用，也不會有 API 金鑰問題
    """
    lines = []

    sox = us.get('SOX', {})
    nvda = us.get('NVDA', {})
    dxy  = us.get('DXY', {})
    us10y= us.get('US10Y', {})
    nq   = us.get('NASDAQ', {})

    # 整體判斷
    score = 0
    if sox.get('changePct', 0) > 1:   score += 2
    elif sox.get('changePct', 0) > 0:  score += 1
    elif sox.get('changePct', 0) < -1: score -= 2
    else:                              score -= 1

    if nvda.get('changePct', 0) > 2:   score += 1
    elif nvda.get('changePct', 0) < -2: score -= 1

    if dxy.get('changePct', 0) < -0.3:  score += 1   # 美元弱→好
    elif dxy.get('changePct', 0) > 0.3:  score -= 1

    if us10y.get('price', 4.5) < 4.0:  score += 1
    elif us10y.get('price', 4.5) > 4.8: score -= 1

    if score >= 3:
        outlook = "🟢 偏多"
        summary = "美股昨晚表現強勁，台股今日開盤預計跟漲，科技股尤其值得關注。"
    elif score >= 1:
        outlook = "🟢 小幅偏多"
        summary = "美股昨晚小漲，台股今日開盤預計溫和上漲，整體氣氛尚可。"
    elif score == 0:
        outlook = "🟡 中性"
        summary = "美股昨晚漲跌互見，台股今日開盤方向不明，建議觀望為主。"
    elif score >= -2:
        outlook = "🔴 小幅偏空"
        summary = "美股昨晚偏弱，台股今日開盤預計承壓，注意持股變化。"
    else:
        outlook = "🔴 偏空"
        summary = "美股昨晚明顯下跌，台股今日開盤預計跟跌，建議謹慎。"

    # 個別指標說明
    details = []
    sox_pct = sox.get('changePct', 0)
    details.append(f"費半 {'+' if sox_pct>=0 else ''}{sox_pct}%（{'正面' if sox_pct>0 else '負面'}訊號，直接影響台積電等半導體股）")

    nvda_pct = nvda.get('changePct', 0)
    details.append(f"輝達 {'+' if nvda_pct>=0 else ''}{nvda_pct}%（{'AI概念股跟漲' if nvda_pct>0 else 'AI族群可能承壓'}）")

    dxy_pct = dxy.get('changePct', 0)
    details.append(f"美元指數 {'+' if dxy_pct>=0 else ''}{dxy_pct}%（{'美元強，外資撤台灣壓力增' if dxy_pct>0.2 else '美元弱，有利外資留台灣' if dxy_pct<-0.2 else '美元平穩'}）")

    y10 = us10y.get('price', 0)
    details.append(f"美國10年期公債 {y10}%（{'偏高，資金壓力大' if y10>4.8 else '正常範圍' if y10>3.8 else '偏低，有利股市'}）")

    if usd_twd:
        details.append(f"台幣匯率 {usd_twd}（{'台幣偏弱，外資匯損壓力' if usd_twd>32 else '台幣偏強，有利外資留台' if usd_twd<30 else '匯率平穩'}）")

    return {
        'outlook': outlook,
        'summary': summary,
        'details': details,
        'score'  : score,
    }

def run_premarket():
    log("=== 開盤前任務開始 ===")
    us      = fetch_us_markets()
    usd_twd = fetch_usd_twd()
    preview = build_premarket_summary(us, usd_twd)

    # 讀取現有 data.json（保留收盤資料，只更新美股部分）
    out_path   = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data.json')
    existing   = {}
    if os.path.exists(out_path):
        try:
            with open(out_path, 'r', encoding='utf-8') as f:
                existing = json.load(f)
        except:
            pass

    existing.update({
        "mode"         : "premarket",
        "premarketAt"  : tw_now().strftime('%Y/%m/%d %H:%M'),
        "usMarkets"    : us,
        "twMarket"     : {
            **existing.get('twMarket', {}),
            "usdTwd"   : usd_twd,
        },
        "openingPreview": preview,
    })

    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    log(f"=== 開盤前任務完成，預測：{preview['outlook']} ===")

# ════════════════════════════════════════════════════
# 收盤後任務：台股收盤價 + 新聞
# ════════════════════════════════════════════════════

def fetch_tw_stocks(codes):
    result = {}
    today  = datetime.datetime.now().strftime('%Y%m%d')

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
                        prev  = safe_float(row[3]); close = safe_float(row[6])
                        chg   = round(close - prev, 2)
                        pct   = round((chg / prev * 100), 2) if prev else 0
                        result[code] = {
                            'name': row[1].strip(), 'price': close, 'prev': prev,
                            'high': safe_float(row[4]), 'low': safe_float(row[5]),
                            'change': chg, 'changePct': pct,
                            'vol': row[2] + '張', 'source': 'TWSE'
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
            d_str = datetime.datetime.now().strftime('%Y/%m/%d')
            url2  = (f"https://www.tpex.org.tw/web/stock/aftertrading/"
                     f"otc_quotes_no1430/stk_wn1430_result.php?l=zh-tw&d={d_str}&se=EW")
            r2    = requests.get(url2, timeout=15, headers=HEADERS)
            for row in r2.json().get('aaData', []):
                code = row[0].strip()
                if code in otc_codes:
                    try:
                        close = safe_float(row[2]); chg = safe_float(row[3])
                        prev  = round(close - chg, 2)
                        pct   = round((chg / prev * 100), 2) if prev else 0
                        result[code] = {
                            'name': row[1].strip(), 'price': close, 'prev': prev,
                            'high': safe_float(row[4]), 'low': safe_float(row[5]),
                            'change': chg, 'changePct': pct,
                            'vol': row[7] + '張', 'source': 'OTC'
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
            url = (f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{sym}"
                   f"?modules=defaultKeyStatistics,summaryDetail")
            r   = requests.get(url, timeout=8, headers=HEADERS)
            res = r.json()['quoteSummary']['result'][0]
            pe  = (res.get('summaryDetail',{}).get('trailingPE',{}).get('raw')
                   or res.get('defaultKeyStatistics',{}).get('forwardPE',{}).get('raw'))
            if pe:
                stocks_data[code]['pe'] = round(pe, 1)
            dy = res.get('summaryDetail',{}).get('dividendYield',{}).get('raw')
            if dy:
                stocks_data[code]['dividendYield'] = round(dy * 100, 2)
            log(f"  {code}: PE={stocks_data[code].get('pe','-')} 殖利率={stocks_data[code].get('dividendYield','-')}%")
            time.sleep(0.4)
        except Exception as e:
            log(f"  {code} PE 失敗: {e}")
    return stocks_data

def fetch_foreign_buy():
    try:
        log("抓取外資買賣超...")
        r    = requests.get("https://www.twse.com.tw/fund/TWT38U?response=json&selectType=ALLBUT0999",
                            timeout=10, headers=HEADERS)
        rows = r.json().get('data', [])
        if rows:
            last = rows[-1]
            buy  = safe_float(str(last[4]).replace(',',''))
            sell = safe_float(str(last[5]).replace(',',''))
            net  = round((buy - sell) / 100000000, 1)
            log(f"  外資: {'+' if net>=0 else ''}{net} 億")
            return net
    except Exception as e:
        log(f"  外資失敗: {e}")
    return None

def fetch_yahoo_news(code):
    news = []
    try:
        sym = f"{code}.TW"
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={sym}&newsCount=5&lang=zh-TW"
        r   = requests.get(url, timeout=10, headers=HEADERS)
        for item in r.json().get('news', [])[:MAX_NEWS]:
            title = item.get('title','').strip()
            pt    = item.get('providerPublishTime', 0)
            ds    = datetime.datetime.fromtimestamp(pt).strftime('%Y/%m/%d') if pt else ''
            if title:
                news.append(f"{title}（{ds}）" if ds else title)
        time.sleep(0.3)
    except Exception as e:
        log(f"  {code} Yahoo新聞失敗: {e}")
    return news

def fetch_mops_news(code):
    news = []
    try:
        now = datetime.datetime.now()
        d1  = (now - datetime.timedelta(days=60)).strftime('%Y%m%d')
        d2  = now.strftime('%Y%m%d')
        url = (f"https://mops.twse.com.tw/mops/web/ajax_t05st01"
               f"?encodeURIComponent=1&step=1&firstin=1&off=1"
               f"&keyword4=&code1=&TYPEK=all&co_id={code}&date1={d1}&date2={d2}")
        r   = requests.post(url, timeout=12,
                            headers={**HEADERS, 'Referer':'https://mops.twse.com.tw/'})
        count = 0
        for row in re.findall(r'<tr[^>]*>(.*?)</tr>', r.text, re.DOTALL):
            cells = [re.sub(r'<[^>]+>','',c).strip()
                     for c in re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)]
            cells = [c for c in cells if c]
            if len(cells) >= 3:
                dp = cells[0].strip(); tp = cells[2].strip()
                if any(k in dp for k in ['發言','日期','序號']): continue
                if dp and tp and len(tp) > 4:
                    news.append(f"{tp}（{dp}）")
                    count += 1
                    if count >= MAX_NEWS: break
    except Exception as e:
        log(f"  {code} MOPS失敗: {e}")
    return news

def fetch_all_news(codes):
    all_news = {}
    log("抓取個股新聞...")
    for code in codes:
        log(f"  → {code}")
        news = []
        for fetcher in [fetch_yahoo_news, fetch_mops_news]:
            if len(news) >= MAX_NEWS: break
            for item in fetcher(code):
                if item not in news:
                    news.append(item)
                if len(news) >= MAX_NEWS: break
        all_news[code] = news[:MAX_NEWS]
        log(f"    共 {len(all_news[code])} 則")
        time.sleep(0.5)
    return all_news

def fetch_market_news():
    news = []
    try:
        log("抓取大盤新聞...")
        url = "https://query2.finance.yahoo.com/v1/finance/search?q=台股&newsCount=5&lang=zh-TW"
        r   = requests.get(url, timeout=10, headers=HEADERS)
        for item in r.json().get('news', [])[:5]:
            title = item.get('title','').strip()
            pt    = item.get('providerPublishTime', 0)
            ds    = datetime.datetime.fromtimestamp(pt).strftime('%Y/%m/%d') if pt else ''
            if title:
                news.append({'title': title, 'date': ds, 'url': item.get('link','')})
        log(f"  大盤新聞 {len(news)} 則")
    except Exception as e:
        log(f"  大盤新聞失敗: {e}")
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
    }

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data.json')
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
