"""郵件①盤勢總覽與③教學重點的複核工具（管理者 2026/11/06：每日與每週都要審這兩章的品質）。

    python scripts/daily_review/chapters.py check-plan scripts/repairs/<日期>-points-review.json .daily-review/<YYYYMMDD>/raw.txt

一、compare_site.py 呼叫 chapter_points／chapter_diffs：從今天發布的文章拆出兩章的每一點，列出機械可查的疑點
    （章節空白、點數不足、數字不在原稿、過短），其餘語意判斷（幻覺、漏掉的重點、說明不清）由複核者對照凍結的判讀寫進 judged.json。
二、check-plan：推修正分支前，用和 apply_reviewed_points.py 相同的核對先在本機驗一次章節修正計畫
    （原稿指紋、每句引句逐字在原稿、品質概況沒有硬傷、沒有被判成重複的點），並印出郵件會呈現的兩章。不呼叫模型、不寫試算表。
"""
import hashlib
import json
import os
import re
import sys
from pathlib import Path

HEADS = {'macro': '① 盤勢總覽重點整理', 'lessons': '③ 分析師操作邏輯與教學重點'}
NAMES = {'macro': '①盤勢總覽', 'lessons': '③教學重點'}
EMPTY = '本集未整理出'
TARGET = 3            # 每章以 3 點為目標（2026/10/07、10/29）；原稿夠長卻不到就列疑點
LONG_SOURCE = 5000    # 與主流程 LESSON_MIN_SOURCE 一致
SHORT = {'macro': 60, 'lessons': 100}   # 明顯過短（主流程提示詞：盤勢 70～140 字、教學 120～220 字）
NUM = re.compile(r'\d[\d,]*(?:\.\d+)?')


def chapter_points(article):
    """純函式：{'macro': [點, ...], 'lessons': [...]}；章節空白時是空串列。"""
    text = str(article or '')
    out = {}
    for key, head in HEADS.items():
        at = text.find(head)
        if at < 0:
            out[key] = []
            continue
        body = text[at + len(head):]
        stop = re.search(r'\n\s*(?:②|③|④|⑤)\s', body) if key == 'macro' else re.search(r'\n\s*(?:④|⑤|⑥)\s', body)
        body = body[:stop.start()] if stop else body
        out[key] = [l.strip()[1:].strip() for l in body.splitlines() if l.strip().startswith('•') and l.strip()[1:].strip()]
    return out


def _digits(text):
    return re.sub(r'[\s,，]', '', str(text or ''))


def missing_numbers(point, raw):
    """純函式：點裡出現、但原稿（去掉空白與千分位）找不到的數字。只看兩位數以上或有小數點的。"""
    hay = _digits(raw)
    out = []
    for m in NUM.finditer(point):
        n = m.group(0).replace(',', '').rstrip('.')
        if (len(n.replace('.', '')) >= 2 or '.' in n) and n not in hay and n not in out:
            out.append(n)
    return out


def chapter_diffs(article, raw):
    """純函式：機械可查的章節疑點，格式與 compare_site 的差異列相同（代號欄放 ①／③）。"""
    points = chapter_points(article)
    long_enough = len(_digits(raw)) >= LONG_SOURCE
    diffs = []
    for key, items in points.items():
        code, name = ('①', NAMES[key]) if key == 'macro' else ('③', NAMES[key])
        base = {'code': code, 'name': name, 'review': '', 'accept': [], 'quote': '', 'review_why': ''}
        if not items:
            diffs.append(dict(base, site=EMPTY if EMPTY in str(article) else '（找不到這一章）', type='章節空白',
                              site_note='整章沒有任何重點（2026/10/29 管理者：這是非常嚴重的錯誤）'))
            continue
        if long_enough and len(items) < TARGET:
            diffs.append(dict(base, site=f'{len(items)} 點', type='章節點數不足',
                              site_note=f'原稿 {len(_digits(raw))} 字，這一章只有 {len(items)} 點（目標 {TARGET} 點）'))
        for p in items:
            gone = missing_numbers(p, raw)
            if gone:
                diffs.append(dict(base, site=p[:60], type='章節數字不在原稿', site_note=p,
                                  review_why='原稿找不到：' + '、'.join(gone)))
            if len(p) < SHORT[key]:
                diffs.append(dict(base, site=p[:60], type='章節過短', site_note=p,
                                  review_why=f'{len(p)} 字（主流程規格：盤勢 70～140 字、教學 120～220 字）'))
    return diffs


def chapters_markdown(article, reading_chapters):
    """給複核者並排看：網站的兩章每一點，與凍結判讀裡自己整理的主題。"""
    points = chapter_points(article)
    lines = ['# 章節複核：網站 vs 凍結判讀', '']
    for key in ('macro', 'lessons'):
        lines += [f'## {NAMES[key]}', '', '網站：']
        lines += [f'{i}. {p}' for i, p in enumerate(points[key], 1)] or ['（空白）']
        lines += ['', '凍結判讀的主題：']
        lines += [f"- {t.get('topic', '')}｜{t.get('detail', '')}｜原句：{str(t.get('quote', ''))[:60]}"
                  for t in (reading_chapters or {}).get(key) or []] or ['（判讀沒有列）']
        lines.append('')
    return '\n'.join(lines) + '\n'


def check_plan(plan_path, raw_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
    os.environ.setdefault('GEMINI_API_KEY', 'offline')
    os.environ.setdefault('SPREADSHEET_ID', 'offline')
    import apply_reviewed_points as arp
    pl = arp.pl
    raw = Path(raw_path).read_text(encoding='utf-8')
    plan = json.loads(Path(plan_path).read_text(encoding='utf-8'))
    reviewed = plan['public_points_review']
    problems = []
    if hashlib.sha256(raw.encode()).hexdigest() != reviewed.get('sha256'):
        problems.append('sha256 與 raw.txt 不符（要用 status.json 的 raw_sha256）')
    points = []
    for item in reviewed.get('points') or []:
        p = dict(item)
        if p.get('kind') not in ('level', 'volume', 'event', 'flow', 'view'):
            problems.append(f"kind 不合法：{p.get('kind')}（盤勢用 level/volume/event/flow，教學用 view）")
        try:
            p['evidence'] = [arp.source_quote(raw, q) for q in p.pop('quotes')]
        except (ValueError, KeyError) as e:
            problems.append(str(e))
            continue
        gone = missing_numbers(p.get('text', ''), ' '.join(p['evidence']))
        if gone:
            problems.append(f"「{p.get('text', '')[:24]}…」的數字不在自己的引句：{'、'.join(gone)}")
        p.update(_evidence_verified=True, _manual_public_review=True)
        points.append(p)
    if points:
        points[0].update(headline=reviewed.get('headline', ''), _headline_checked=True)
        signals = {'market': points}
        hard, _soft = pl.print_quality_overview(signals, raw)
        problems += ['品質概況：' + h for h in hard]
        article = pl.canonical_article(signals, plan.get('date', ''))
        problems += [f"被判成重複、不會出現在郵件：{p['text'][:30]}" for p in points if p.get('_duplicate_point')]
        shown = chapter_points(article)
        for key in ('macro', 'lessons'):
            if len(shown[key]) < TARGET:
                problems.append(f'{NAMES[key]}只有 {len(shown[key])} 點（目標 {TARGET} 點）')
        print(article.split('②')[0].strip())
        print()
        print('③' + article.split('③')[-1])
    for p in problems:
        print('✗', p)
    print('章節修正計畫核對通過' if not problems else f'章節修正計畫不通過（{len(problems)} 項），不要推送')
    return not problems


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == 'check-plan':
        sys.exit(0 if check_plan(sys.argv[2], sys.argv[3]) else 1)
    raise SystemExit('用法：python scripts/daily_review/chapters.py check-plan <計畫.json> <raw.txt>')
