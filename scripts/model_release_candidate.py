"""模型候選發布的純資料契約；參考答案只供事後評分，不能出現在推論輸入。"""
import copy
import hashlib
import json

VERSION = 'model-candidate-v1'
TABLES = ('操作紀錄', '會員持股', '每日推播內容')
LABELS = {'buy': '買入', 'sell': '賣出', 'watch_watch': '觀望注意', 'watch_avoid': '觀望不碰'}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def seal(payload):
    return {'payload': copy.deepcopy(payload), 'sha256': digest(payload)}


def validate(envelope, *, day, commit, source_sha, state, reference_sha):
    p = envelope.get('payload') or {}
    if envelope.get('sha256') != digest(p):
        raise ValueError('候選內容指紋不符')
    if (p.get('version') != VERSION or p.get('date') != day or p.get('commit') != commit
            or p.get('source_sha256') != source_sha or p.get('state') != state
            or p.get('reference_sha256') != reference_sha):
        raise ValueError('候選版本、原稿、參考政策或工作表快照已變，須重新預覽')
    if (p.get('mode') != 'fresh_model_only' or p.get('answer_injection') is not False
            or p.get('status') != 'passed' or p.get('errors') or not p.get('reference_sha256')
            or not p.get('run_id') or not p.get('model_calls', 0) > 0):
        raise ValueError('候選未經真實模型與事後參考驗收，禁止發布')
    if not isinstance(p.get('signals'), dict) or not p.get('article'):
        raise ValueError('候選缺少完整分類／文章')
    return copy.deepcopy(p)


def inference_values(name, values, day, norm_date):
    """同日既有答案與未來部位不進模型；通知只保留事件欄，既有公開說明清空。"""
    rows = copy.deepcopy(values)
    if not rows:
        return rows
    head = rows[0]
    if name in ('分類裁決', '修正建議', '每日推播內容', '持股追蹤', '逐字稿判讀稽核'):
        return [head]
    if name == '會員簡訊':
        # 原文才是通知依據；已解析說明可能經人工重寫，不能再作模型答案。
        if '解析明細' in head:
            col = head.index('解析明細')
            for cells in rows[1:]:
                if col < len(cells):
                    cells[col] = '[]'
        return rows
    if name not in ('操作紀錄', '會員持股') or '日期' not in head:
        return rows
    kept = [head]
    for cells in rows[1:]:
        r = dict(zip(head, cells))
        date = norm_date(r.get('日期', ''))
        if date and date > day:
            continue
        if date == day:
            if not str(r.get('來源影片ID', '')).startswith('CMONEY-'):
                continue
            for field in ('理由摘錄', '說明重點'):
                if field in head and len(cells) > head.index(field):
                    cells[head.index(field)] = ''
        kept.append(cells)
    return kept


def plan_tables(tables, signals, article, day, video_id, norm_date, protected, stance_label=str):
    """只重建本日逐字稿列；通知、人工列、其他日期、寄送旗標逐值保留。"""
    result = copy.deepcopy(tables)
    for name in TABLES[:2]:
        before = tables[name]
        head = before[0]
        remove = [i for i, cells in enumerate(before[1:], 1)
                  if norm_date(dict(zip(head, cells)).get('日期', '')) == day
                  and not protected(dict(zip(head, cells)).get('來源影片ID', ''))]
        kept = [r for i, r in enumerate(before) if i not in remove]
        existing = set()
        for cells in kept[1:]:
            r = dict(zip(head, cells))
            if norm_date(r.get('日期', '')) == day:
                existing.add((str(r.get('代號', '')), r.get('方向') if name == '操作紀錄' else 'holdings'))
        for cat in (LABELS if name == '操作紀錄' else ('holdings',)):
            for row in signals.get(cat, []) or []:
                if (row.get('_date') or day) != day:
                    raise ValueError('候選含跨日交易，須另行驗收，不在單日發布中回填')
                code = str(row.get('code') or '')
                if not code or not code.isdigit():
                    raise ValueError('候選含待確認股票身分')
                label = LABELS.get(cat, 'holdings')
                if (code, label) in existing:
                    continue
                item = {'日期': day, '股票名稱': row.get('name', ''), '代號': code,
                        '來源影片ID': video_id, '方向': label,
                        '價位說明': row.get('price') or '未說明',
                        '理由摘錄': row.get('reason') or '未說明',
                        '說明重點': row.get('note') or row.get('reason') or '未說明',
                        '序': str(100 + int(row.get('_seq', 1)) if int(row.get('_seq', 1)) < 100 else int(row['_seq']))}
                for key in ('目前立場', '持有態度', '態度', '立場'):
                    item[key] = stance_label(row.get('stance') or '續抱')
                kept.append([str(item.get(h, '')) for h in head])
                existing.add((code, label))
        result[name] = kept
    daily = result['每日推播內容']
    indices = [i for i, r in enumerate(daily[1:], 1) if r and norm_date(r[0]) == day]
    if len(indices) != 1:
        raise ValueError('每日文章必須有唯一既有列；不在此流程建立或改動寄送狀態')
    daily[indices[0]][1] = article
    return result


def cell_requests(before, after, ids):
    """單次 Sheets batchUpdate 原子套用預覽後的精確儲存格；相同值不重寫。"""
    requests = []
    for name in TABLES:
        a, b = before[name], after[name]
        width = len(a[0])
        if a[0] != b[0]:
            raise ValueError('禁止變更表頭')
        for i in range(1, max(len(a), len(b))):
            old = list(a[i]) if i < len(a) else []
            new = list(b[i]) if i < len(b) else []
            old += [''] * (width - len(old))
            new += [''] * (width - len(new))
            for j, (x, y) in enumerate(zip(old, new)):
                if str(x) != str(y):
                    requests.append({'updateCells': {'range': {'sheetId': ids[name],
                        'startRowIndex': i, 'endRowIndex': i + 1,
                        'startColumnIndex': j, 'endColumnIndex': j + 1},
                        'rows': [{'values': [{'userEnteredValue': {'stringValue': str(y)}}]}],
                        'fields': 'userEnteredValue'}})
    return requests
