"""第三步：判讀凍結後，讀今天實際發布的內容逐檔比對，產生差異表（不判斷對錯，只列出來）。

    python scripts/daily_review/compare_site.py [--date YYYY/MM/DD]

讀：操作紀錄／會員持股的今天列（網站與郵件就是由它們組成）、每日推播內容的文章。
寫：.daily-review/<日期>/site.json、diffs.json、diffs.md
差異類型：分類、漏收、複核未涵蓋、應排除卻公開、今日買賣、通知用語外洩、說明重複
"""
import argparse
import json
import re

from common import LABEL, WORK, DIRECTION, norm_day, open_book, pipeline, read_json, rows_of, sha, today, write_json


def grams(text, n=3):
    t = re.sub(r'[\s，,。！？!?；;：:「」（）()、．.…—\-*•]', '', str(text or ''))
    return {t[i:i + n] for i in range(len(t) - n + 1)}


STOP = set('的了是在有我你他她它們就也都要會不和與及或而但這那一個些很最還再已把被讓從到對為於以')
# 文章固定標題與每天都會出現的一般用語：不是通知專屬的內容
COMMON = ('盤勢總覽重點整理 會員操作紀錄與持股明細 當日明確說明之買入賣出紀錄 影片中明講之會員目前持有股票 觀望個股當日未執行買賣'
          ' 語氣偏空暫不進場 語氣偏多留意追蹤 分析師操作邏輯與教學重點 風險揭露與重要提醒 僅供參考 持股續抱 目前許多投資會員股票操作')


def notice_only_phrases(public, raw, notices, polished=''):
    """公開文字裡、只在會員通知出現而原稿／修飾稿都沒有的詞（兩字片段，排除含常用虛字的），併成連續片語。

    2026/11/02：郵件盤勢與教學出現了只在當天私人會員通知裡的用語。
    只是候選，由複核判斷是不是真的外洩（例如通知與原稿用不同說法講同一件事就不算）。"""
    source = grams(raw, 2) | grams(polished, 2) | grams(COMMON, 2)
    leak = {g for g in grams(public, 2) & grams(notices, 2)
            if g not in source and not (set(g) & STOP) and not re.search(r'\d', g)}
    flat = re.sub(r'[\s，,。！？!?；;：:「」（）()、．.…—\-*•]', '', public)
    phrases, i = [], 0
    while i < len(flat) - 1:
        if flat[i:i + 2] in leak:
            j = i + 2
            while j < len(flat) and flat[j - 1:j + 1] in leak:
                j += 1
            if flat[i:j] not in phrases:
                phrases.append(flat[i:j])
            i = j
        else:
            i += 1
    return phrases


def duplicated_sentence(note):
    parts = [p for p in re.split(r'[。！？!?]', str(note or '')) if len(p) >= 8]
    for i, a in enumerate(parts):
        for b in parts[i + 1:]:
            ga, gb = grams(a, 2), grams(b, 2)
            if ga and gb and len(ga & gb) / min(len(ga), len(gb)) >= 0.6:
                return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default='')
    a = ap.parse_args()
    day = norm_day(a.date) if a.date else today()
    out = WORK / day.replace('/', '')
    reading = read_json(out / 'reading.json')
    if not (out / 'reading.sha256').exists():
        raise SystemExit('判讀還沒凍結：先跑 freeze_reading.py')
    pl = pipeline()
    book = open_book(pl)

    site = {}
    head, rows = rows_of(book.worksheet('操作紀錄'))
    for r in rows:
        if norm_day(r.get('日期')) == day and str(r.get('代號') or '').strip():
            cat = DIRECTION.get(str(r.get('方向') or ''))
            if cat:
                site.setdefault(str(r['代號']).strip(), []).append(
                    {'category': cat, 'name': r.get('股票名稱'), 'note': r.get('理由摘錄'), 'source': r.get('來源影片ID')})
    head, rows = rows_of(book.worksheet('會員持股'))
    for r in rows:
        if norm_day(r.get('日期')) == day and str(r.get('代號') or '').strip():
            site.setdefault(str(r['代號']).strip(), []).append(
                {'category': 'holdings', 'name': r.get('股票名稱'), 'note': r.get('說明重點'), 'source': r.get('來源影片ID')})
    _, arts = rows_of(book.worksheet('每日推播內容'))
    article = next((str(r.get('文字稿') or '') for r in arts if norm_day(r.get('日期')) == day), '')
    write_json(out / 'site.json', {'rows': site, 'article_sha256': sha(article), 'article': article})

    diffs = []
    reviewed = {}
    for s in reading.get('stocks') or []:
        reviewed[str(s['code'])] = s
        cats = [x['category'] for x in site.get(str(s['code']), [])]
        public = [c for c in cats if c in ('buy', 'sell', 'holdings', 'watch_watch', 'watch_avoid')]
        accept = s['accept']
        shown = public or ['missing']
        hidden_ok = bool(set(accept) & {'history', 'ignored', 'uncertain'})
        if all(c in accept for c in public) and (public or hidden_ok):
            continue
        kind = '漏收' if not public else ('今日買賣' if set(public) & {'buy', 'sell'} or set(accept) & {'buy', 'sell'} else '分類')
        diffs.append({'code': s['code'], 'name': s['name'], 'site': '、'.join(LABEL[c] for c in shown),
                      'review': '／'.join(LABEL[c] for c in accept), 'accept': accept, 'type': kind,
                      'quote': s.get('quote', ''), 'review_why': s.get('why', ''),
                      'site_note': ' ／ '.join(str(x['note'] or '') for x in site.get(str(s['code']), []))})
    blocked = {str(x.get('code') or '') for x in reading.get('must_not_public') or []}
    for code, items in site.items():
        public = [x for x in items if x['category'] in ('buy', 'sell', 'holdings', 'watch_watch', 'watch_avoid')]
        if code in reviewed or not public:
            continue
        diffs.append({'code': code, 'name': public[0]['name'], 'site': '、'.join(LABEL[x['category']] for x in public),
                      'review': '（判讀未涵蓋）', 'accept': [], 'type': '應排除卻公開' if code in blocked else '複核未涵蓋',
                      'quote': '', 'review_why': '', 'site_note': public[0]['note'],
                      'source': public[0].get('source')})
    for code, items in site.items():
        for x in items:
            if duplicated_sentence(x['note']):
                diffs.append({'code': code, 'name': x['name'], 'site': LABEL[x['category']], 'review': '',
                              'accept': [], 'type': '說明重複', 'quote': '', 'review_why': '', 'site_note': x['note']})
    raw = (out / 'raw.txt').read_text(encoding='utf-8')
    polished = (out / 'polished.txt').read_text(encoding='utf-8') if (out / 'polished.txt').exists() else ''
    notices = (out / 'notices.txt').read_text(encoding='utf-8')
    public_text = article + '\n'.join(str(x['note'] or '') for items in site.values() for x in items)
    for phrase in notice_only_phrases(public_text, raw, notices, polished):
        diffs.append({'code': '—', 'name': '公開文字', 'site': phrase, 'review': '', 'accept': [],
                      'type': '通知用語外洩', 'quote': '', 'review_why': '只出現在當日會員通知，原稿與修飾稿都沒有',
                      'site_note': ''})
    write_json(out / 'diffs.json', diffs)
    lines = [f'# {day} 比對差異（{len(diffs)} 項；判讀 {len(reviewed)} 檔，網站 {len(site)} 檔）', '',
             '| 代號 | 名稱 | 網站 | 複核 | 類型 | 原句 | 網站說明 |', '|---|---|---|---|---|---|---|']
    for d in diffs:
        lines.append(f"| {d['code']} | {d['name']} | {d['site']} | {d['review']} | {d['type']} | {d['quote'][:40]} | {str(d['site_note'])[:60]} |")
    (out / 'diffs.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
