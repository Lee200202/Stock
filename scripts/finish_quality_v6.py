from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
p=ROOT/'pipeline/pipeline.py';s=p.read_text(encoding='utf-8')
start=s.index('def price_reality_check(');end=s.index('\n# ---------------------------------------------------------------- #\n# 日期歸屬',start)
s=s[:start]+'''def price_reality_check(ss, signals: dict, date_str: str) -> dict:
    """價位與持有事實分開查；無日K仍檢查格式，不再猜倍數或改股票方向。"""
    for cat in SIGNAL_CATEGORIES:
        for row in signals.get(cat, []):
            value = str(row.get('price') or '')
            context = row.get('reason') or row.get('note') or ''
            formatted = display_price(value, row.get('price_evidence') or '', context)
            if formatted == '未說明' or cat in ('watch_watch','watch_avoid'):
                row['price'] = formatted
            if formatted == '未說明' and value.strip() in ('5','10','20','60','120','200','240') and re.search(r'均線|技術線|碰.{0,6}線|線型', context):
                for field in ('reason','note'):
                    if row.get(field):
                        row[field] = re.sub(r'(?<!\\d)' + re.escape(value.strip()) + r'(?!\\d)(?:附近)?', '所提技術位置', row[field])
    try:
        kmap = _daily_k_cached(ss)
    except Exception as exc:
        print(f'  價位已做本機格式檢查；日K區間暫無法核對：{exc}')
        kmap = {}
    for cat in SIGNAL_CATEGORIES:
        for row in signals.get(cat, []):
            code = str(row.get('code') or '')
            band = _price_band(kmap, code, date_str)
            value = display_price(row.get('price'), row.get('price_evidence'), row.get('reason'))
            numbers = _all_prices(value)
            if not band or not numbers:
                continue
            high, low = band
            if not low * (1-PRICE_CLEAR_BAND) <= numbers[0] <= high * (1+PRICE_CLEAR_BAND):
                row['price'] = '未說明'
                note_decision('價位校對','價位超出快取區間，未補猜',row.get('name',''),value)
                print(f'  價位校對 {row.get("name", code)}：{value} 超出 {low}-{high}，價位未說明；分類維持原文判定')
    return signals
''' +s[end:]
s=s.replace("    his = [float(v[0]) for v in days.values() if v and v[0]]\n    los = [float(v[1]) for v in days.values() if v and v[1]]", "    # 重跑歷史逐字稿不能偷用未來的行情驗價。\n    past = [v for day,v in days.items() if norm_date(day) and norm_date(day) <= date_str]\n    his = [float(v[0]) for v in past if v and v[0]]\n    los = [float(v[1]) for v in past if v and v[1]]")
s=s.replace("'宜頂': ('5289','宜鼎')}","'宜頂': ('5289','宜鼎'), '移頂': ('5289','宜鼎'), '以頂': ('5289','宜鼎')}")
s=s.replace('宜頂＝宜鼎5289','宜頂、移頂、以頂＝宜鼎5289')
p.write_text(s,encoding='utf-8')
p=ROOT/'apps-script/Adminpipeline.gs';s=p.read_text(encoding='utf-8').replace("'宜頂':['5289','宜鼎']", "'宜頂':['5289','宜鼎'], '移頂':['5289','宜鼎'], '以頂':['5289','宜鼎']");p.write_text(s,encoding='utf-8')
print('Removed old price scaling/demotion branch; preserved factual classifications and original transaction ranges.')
