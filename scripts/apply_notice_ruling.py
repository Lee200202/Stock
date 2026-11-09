"""管理者裁示：依會員通知（簡訊）方向，改正由通知產生的操作紀錄列「方向」與理由（2026/11/09 管理者核准）。

背景：11/09 簡訊明寫兩次「請買進 8210 勤誠，於 570 元以下買進」，解析卻列成「觀望注意」；
管理者裁示「依簡訊方向判定，簡訊優先；買入與會員持股同時成立就兩列都留」。
既有工具都不改通知列（apply_source_review 拒改 CMONEY-／SMS- 來源；apply_notice_reason_fix 只改措辭），
所以裁示走這支：只改同一列的「方向」與「理由摘錄」，以及會員簡訊「解析明細」同一項的 dir／reason。

守門：
- 一定要有管理者裁示授權（authorization 含「管理者」、ruling_ids 非空）。
- 計畫用通知內容指紋（notice_sha256，等於會員簡訊「內容指紋」）綁定那一則通知；通知原文不寫進公開 repo。
- 改成買入／賣出時，通知裡這一檔的句子必須有明確執行指令（請／應／務必／建議＋買進／賣出，或資金轉為買進），
  而且沒有「不急著買」「條件未到」這類禁令；價位說明裡的數字也要出自同一段通知。改成觀望只要求通知有點名本股。
- 只在操作紀錄內改方向（買入、賣出、觀望注意、觀望不碰都在同一張表），不跨表、不新增或刪除列、不動價位、
  來源、通知原文、寄送帳本；同一天同一檔已經有其他來源的同方向列時不改（避免重複成交）。
- 新理由要有逐字出自當天原稿的引句（quotes），不得含通知／通道字眼；舊理由只存指紋。
寫入前先做獨立備份，寫完逐欄讀回，再刷新郵件內容／持股追蹤／績效；寄送狀態與帳本不得變動。
計畫：scripts/repairs/<日期>-notice-ruling.json。不加 --apply 只預覽。"""
import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
sys.path.insert(0, str(ROOT / 'scripts'))
from apply_notice_reason_fix import BANNED, digest, norm_day  # noqa: E402

DIRECTIONS = ('買入', '賣出', '觀望注意', '觀望不碰')
BANNED_RULING = BANNED + ('若想買', '想買股', '可留意')
VERB = {'買入': r'(?:買進|買入|買回|加碼|站買方)', '賣出': r'(?:賣出|賣掉|減碼|出清|站賣方|獲利了結)'}
BLOCKED = re.compile(r'(?:不急(?:於|著)?|不要急(?:於|著)?|勿|不必|不能|不可|不宜|不要).{0,6}(?:買回|買進|買入|追買|追高)|'
                     r'(?:條件|買點).{0,8}(?:尚未|還未|未到|未出現|未成熟)')


def sha(text):
    return hashlib.sha256(str(text).encode('utf-8')).hexdigest()


def strip_prefix(body):
    """去掉廣播序號（張震-1:、震1、張震6GJ-1：）。"""
    return re.sub(r'^\s*(?:張震|震)[0-9A-Za-z]*(?:-\d+)?\s*[:：]?\s*', '', str(body or ''))


def own_context(body, name, code):
    """通知裡講到本股的句子（以句號、驚嘆號、分號切；逗號不切，條件與指令常在同一句）。"""
    stem = re.sub(r'(?:[-＊*]|KY)+$', '', str(name)).strip()
    forms = [x for x in (str(name), stem, str(code)) if len(x) >= 2]
    return '。'.join(s for s in re.split(r'[。！？!?；;\n]', strip_prefix(body)) if any(x in s for x in forms))


def notice_supports(body, name, code, direction, price_text=''):
    """純函式：通知是否支持把本股列成 direction。回傳 (True, '') 或 (False, 原因)。原因不含通知原文。"""
    own = own_context(body, name, code)
    if not own:
        return False, '通知沒有點名這一檔'
    if direction in VERB:
        verb = VERB[direction]
        order = (re.search(r'(?:請|應|務必|建議)[^。]{0,25}' + verb, own) or
                 (direction == '買入' and re.search(r'(?:資金|轉為).{0,12}買進', own)) or
                 re.search(r'(?:市價|[0-9.]+元(?:以上|以下))[^。]{0,8}' + verb, own))
        if not order:
            return False, '通知裡這一檔沒有明確的' + direction + '指令'
        if direction == '買入' and BLOCKED.search(own):
            return False, '通知裡這一檔有暫不買進的條件'
        for num in re.findall(r'\d+(?:\.\d+)?', str(price_text or '')):
            if num not in own:
                return False, '價位 ' + num + ' 不在通知這一檔的句子裡'
    return True, ''


def check_plan(plan, day):
    if plan.get('date') != day:
        raise ValueError('計畫日期不一致')
    if '管理者' not in str(plan.get('authorization') or '') or not plan.get('ruling_ids') or not plan.get('review_basis'):
        raise ValueError('缺少管理者裁示授權（authorization 含「管理者」、ruling_ids、review_basis）')
    if not plan.get('items'):
        raise ValueError('計畫沒有任何項目')
    for item in plan['items']:
        if 'previous_reason' in item or 'notice_text' in item:
            raise ValueError('計畫不得寫入舊理由或通知原文；只放指紋')
        for k in ('code', 'name', 'notice_source_id', 'article_id', 'notice_sha256', 'previous_direction', 'direction',
                  'previous_reason_sha256', 'previous_reason_chars', 'text', 'quotes', 'rationale'):
            if not item.get(k):
                raise ValueError('項目缺少 ' + k + '：' + str(item.get('code')))
        if item['direction'] not in DIRECTIONS or item['previous_direction'] not in DIRECTIONS:
            raise ValueError('方向只能是 ' + '、'.join(DIRECTIONS))
        if item['direction'] == item['previous_direction']:
            raise ValueError('方向沒有改變；只改措辭請用 apply_notice_reason_fix')
        if item['notice_source_id'] != 'CMONEY-' + item['article_id']:
            raise ValueError('notice_source_id 必須是 CMONEY-<文章ID>')
        if any(b in item['text'] for b in BANNED_RULING):
            raise ValueError('新理由仍含通知或通道字眼：' + item['text'])


def locate(trades, sms, day, item):
    th = trades[0]
    rows = [(i, dict(zip(th, c))) for i, c in enumerate(trades[1:], 2)]
    mine = [(i, r) for i, r in rows if norm_day(r.get('日期')) == day and str(r.get('代號', '')).strip() == item['code']]
    hit = [(i, r) for i, r in mine if r.get('來源影片ID') == item['notice_source_id']]
    if len(hit) != 1:
        raise ValueError('通知列不存在或不唯一：' + item['code'])
    ti, trow = hit[0]
    others = [r for i, r in mine if i != ti and r.get('方向') == item['direction']]
    sh = sms[0]
    shit = [(i, dict(zip(sh, c))) for i, c in enumerate(sms[1:], 2) if str(dict(zip(sh, c)).get('文章ID', '')).strip() == item['article_id']]
    if len(shit) != 1:
        raise ValueError('會員簡訊列不存在或不唯一：' + item['article_id'])
    si, srow = shit[0]
    detail = json.loads(srow.get('解析明細') or '[]')
    return ti, trow, others, si, srow, detail


def detail_item(detail, item, direction):
    hit = [d for d in detail if str(d.get('code')) == item['code'] and str(d.get('dir') or '') == direction]
    return hit[0] if len(hit) == 1 else None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--date', required=True)
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    # 雲端環境的金鑰是 Base64 一行；GitHub Secret 是 JSON。匯入共用工具會先還原成 JSON（雲端預覽也能跑）。
    sys.path.insert(0, str(ROOT / 'scripts' / 'daily_review'))
    import common  # noqa: F401
    import pipeline as pl
    from apply_reviewed_points import source_quote
    day = pl.norm_date(args.date)
    path = ROOT / 'scripts/repairs' / (day.replace('/', '-') + '-notice-ruling.json')
    if not path.exists():
        print('沒有通知裁示修正計畫：', path.name)
        return
    plan = json.loads(path.read_text(encoding='utf-8'))
    check_plan(plan, day)
    book = pl.open_sheets()
    source = {}
    raw, _ = pl.existing_transcript(book, '', day, snapshot=source)
    if plan.get('raw_sha256') and (sha(raw) != plan['raw_sha256'] or source.get('video_id') != plan.get('video_id')):
        raise ValueError('原稿指紋／影片已變，未寫入')
    for item in plan['items']:
        for q in item['quotes']:
            source_quote(raw, q)
    ws_t, ws_s, ws_r = book.worksheet('操作紀錄'), book.worksheet('會員簡訊'), book.worksheet('修正建議')
    trades, sms, receipts = (pl.sheets_retry(w.get_all_values) for w in (ws_t, ws_s, ws_r))
    receipt = '通知裁示修正:' + digest(plan)
    done = any(receipt in str(r) for r in receipts[1:])
    names = {w.title for w in book.worksheets()}
    ledgers = [n for n in ('寄送帳本', 'LINE 寄送帳本') if n in names]
    ledger_before = {n: digest(book.worksheet(n).get_all_values()) for n in ledgers}
    sent = pl._daily_article_status(book, day)
    th, sh = trades[0], sms[0]
    writes = []
    for item in plan['items']:
        ti, trow, others, si, srow, detail = locate(trades, sms, day, item)
        if sha(srow.get('原文') or '') != item['notice_sha256'] or str(srow.get('內容指紋') or item['notice_sha256']) != item['notice_sha256']:
            raise ValueError('通知內容已變（指紋不符），未寫入：' + item['code'])
        if done:
            mine = detail_item(detail, item, item['direction'])
            if trow.get('方向') != item['direction'] or trow.get('理由摘錄') != item['text'] or not mine or mine.get('reason') != item['text']:
                raise ValueError('已有收據但資料與計畫不一致：' + item['code'])
            continue
        if trow.get('方向') != item['previous_direction']:
            raise ValueError('網站上這一列的方向已不是 ' + item['previous_direction'] + '，保留現值：' + item['code'])
        if sha(trow.get('理由摘錄') or '') != item['previous_reason_sha256'] or len(trow.get('理由摘錄') or '') != int(item['previous_reason_chars']):
            raise ValueError('理由已被改動，保留現值：' + item['code'])
        if others:
            raise ValueError('同一天這一檔已有其他來源的「' + item['direction'] + '」列，不重複：' + item['code'])
        ok, why = notice_supports(srow.get('原文'), item['name'], item['code'], item['direction'], trow.get('價位說明'))
        if not ok:
            raise ValueError(why + '：' + item['code'])
        mine = detail_item(detail, item, item['previous_direction'])
        if not mine:
            raise ValueError('解析明細沒有唯一對應的項目：' + item['code'])
        new_detail = [dict(d, dir=item['direction'], reason=item['text'], speech_act='order' if item['direction'] in VERB else d.get('speech_act', ''),
                           reviewed='管理者裁示 ' + day + ' ' + ','.join(plan['ruling_ids'])) if d is mine else d for d in detail]
        writes.append((item, ti, si, json.dumps(new_detail, ensure_ascii=False)))
        print('改', item['code'], item['name'], item['previous_direction'], '→', item['direction'], '（價位照舊：' + str(trow.get('價位說明') or '') + '）',
              '\n  舊理由指紋：', item['previous_reason_sha256'][:12], '\n  新：', item['text'])
    if done:
        print('既有收據與資料一致；只接續刷新')
    elif not args.apply:
        print('預覽完成：通知指令、唯一列與原稿引句核對通過；價位、來源、通知原文、寄送帳本不動；模型0、寄送0')
        return
    if args.apply and not done:
        from backup_archive import backup_tables
        backup_tables(pl, book, ('操作紀錄', '會員簡訊', '每日推播內容', '持股追蹤'))
        cell = lambda v: {'userEnteredValue': {'stringValue': str(v)}}
        requests = []
        for item, ti, si, detail_json in writes:
            for col, value in (('方向', item['direction']), ('理由摘錄', item['text'])):
                requests.append({'updateCells': {'start': {'sheetId': ws_t.id, 'rowIndex': ti - 1, 'columnIndex': th.index(col)},
                                                 'rows': [{'values': [cell(value)]}], 'fields': 'userEnteredValue'}})
            requests.append({'updateCells': {'start': {'sheetId': ws_s.id, 'rowIndex': si - 1, 'columnIndex': sh.index('解析明細')},
                                             'rows': [{'values': [cell(detail_json)]}], 'fields': 'userEnteredValue'}})
        stamp = datetime.now(pl.TAIPEI).strftime('%Y/%m/%d %H:%M:%S')
        meta = json.dumps({'receipt': receipt, 'plan': plan}, ensure_ascii=False)
        requests.append({'appendCells': {'sheetId': ws_r.id, 'fields': 'userEnteredValue', 'rows': [{'values': [cell(x) for x in
            [stamp, day, '管理者裁示（依通知方向）', '操作紀錄／會員簡訊解析明細', '', '', meta, '已套用（獨立備份可復原）', stamp]]}]}})
        if digest(ws_t.get_all_values()) != digest(trades) or digest(ws_s.get_all_values()) != digest(sms):
            raise ValueError('備份期間資料變動，未寫入')
        book.batch_update({'requests': requests})
    if not args.apply:
        return
    trades2, sms2 = ws_t.get_all_values(), ws_s.get_all_values()
    for item in plan['items']:
        _, trow, _, _, _, detail = locate(trades2, sms2, day, item)
        mine = detail_item(detail, item, item['direction'])
        if trow.get('方向') != item['direction'] or trow.get('理由摘錄') != item['text'] or not mine or mine.get('reason') != item['text']:
            raise RuntimeError('寫入後讀回不一致：' + item['code'])
    print('操作紀錄方向、理由與解析明細逐欄讀回一致')
    result = pl.maybe_refresh_site(force=True, only=['smsmail', 'tracker', 'perf'], date_str=day) or {}
    if not result.get('ok'):
        raise RuntimeError('資料已保存；只需重跑本模式接續刷新')
    if sent != pl._daily_article_status(book, day) or any(
            digest(book.worksheet(n).get_all_values()) != h for n, h in ledger_before.items()):
        raise RuntimeError('寄送狀態或帳本變動，請核對')
    print('郵件內容／追蹤／績效刷新成功；寄送狀態及帳本未變')


if __name__ == '__main__':
    main()
