"""文章標題的唯讀檢驗：拿已經在站上的日子，用現在的標題步驟各問一次，印出主題、結論、候選與採用的標題。

材料與正式流程相同：那一天的原始逐字稿（開場、主題句、收尾），加上已經公開的盤勢與教學重點
（取自「每日推播內容」裡那一天的文章；重點的原句沒有另外存，所以這裡的用字核對只比重點本身，比正式流程稍嚴）。
一天一個請求，候選都沒通過時多問一次。不寫任何資料、不寄送。
"""
import argparse
import re

from replay_extract import DryBook, pl


def stored_article(book, day):
    try:
        values = pl.sheets_retry(book.worksheet("每日推播內容").get_all_values)
    except Exception as e:
        print(f'讀不到每日推播內容（{type(e).__name__}）')
        return ''
    for row in values[1:]:
        if row and pl.norm_date(row[0]) == day and len(row) > 1:
            return str(row[1] or '')
    return ''


def article_points(article):
    """文章 → (現在的標題, [盤勢重點], [教學重點])。"""
    title = (re.search(r'^(?:[①②③④⑤⑥]\s*)?文章標題\s*[：:]\s*(.+)$', article, re.M) or ['', ''])[1].strip()
    macro = re.search(r'①[^\n]*\n(.*?)(?=\n②|\Z)', article, re.S)
    lesson = re.search(r'③[^\n]*\n(.*)\Z', article, re.S)
    points = lambda block: [l.strip()[1:].strip() for l in (block.group(1) if block else '').splitlines() if l.strip().startswith('•')]
    return title, points(macro), points(lesson)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dates', required=True, help='逗號分隔，YYYY/MM/DD')
    args = parser.parse_args()
    dry = DryBook(pl.open_sheets())
    summary = []
    for raw_day in [d for d in args.dates.split(',') if d.strip()]:
        day = pl.norm_date(raw_day.strip())
        print(f'\n===== {day}')
        transcript, _polished = pl.existing_transcript(dry, '', day, snapshot={})
        article = stored_article(dry, day)
        if not transcript or not article:
            print('  讀不到原始逐字稿或文章，略過')
            continue
        current, macro, lessons = article_points(article)
        if not macro and not lessons:
            print('  文章裡沒有盤勢或教學重點，略過')
            continue
        material = pl.episode_title_material(transcript)
        print(f"  開場 {len(material['opening'])} 字、主題句 {len(material['cues'])} 句、收尾 {len(material['closing'])} 字；盤勢 {len(macro)} 點、教學 {len(lessons)} 點")
        signals = {cat: [] for cat in pl.SIGNAL_CATEGORIES}
        signals['market'] = ([{'kind': 'level', 'text': t, 'evidence': [], '_evidence_verified': True} for t in macro]
                             + [{'kind': 'view', 'text': t, 'evidence': [], '_evidence_verified': True} for t in lessons])
        pl.compose_episode_title(signals, transcript, day)
        new, source, _rejected = pl.article_title_detail(signals)
        composed = any(r.get('_headline_basis') for r in signals['market'])
        print(f"  現在的標題：{current}（{pl._title_chars(current)} 字）")
        print(f"  檢驗的標題：{new if composed else '（沒有候選通過，正式流程會維持原本的標題）'}" + (f"（{pl._title_chars(new)} 字）" if composed else ''))
        summary.append((day, current, new if composed else ''))
    print('\n===== 總表')
    for day, current, new in summary:
        print(f'{day}｜現在：{current}')
        print(f'{" " * len(day)}｜檢驗：{new or "（沒有通過）"}')
    done = sum(1 for _d, _c, n in summary if n)
    print(f'\n{done}／{len(summary)} 天產生了通過核對的標題。')


if __name__ == '__main__':
    main()
