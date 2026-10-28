"""2026/10/28 來源核對修正計畫：依完整原稿與管理者 10/28 裁示產生；每段引句須逐字（不計空白）存在於保存原稿。

用法：python scratch/build_1028_plan.py <原稿檢視紀錄> [<含計畫原列的檢視紀錄>]
第一個檔是 source-review-inspect（尚無計畫時）印出原稿的紀錄；第二個檔有了才填 previous_* 欄位（第一次只產生草稿）。
"""
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOG = pathlib.Path(sys.argv[1]).read_text(encoding='utf-8').splitlines()
a, b = LOG.index('===== 原稿開始 ====='), LOG.index('===== 原稿結束 =====')
RAW = ''.join(''.join(LOG[a + 1:b]).split())          # 只用來核對引句：空白不計，與 source_quote 相同
RAW_SHA = next(l.split()[1] for l in LOG if l.startswith('原稿指紋 '))
VIDEO = json.loads(next(l.split(' ', 1)[1] for l in LOG if l.startswith('原稿來源 ')).replace('***', ''))['video_id'] \
    if any(l.startswith('原稿來源 {') for l in LOG) else 'BR1VPxZ8z-k'
OLD = {}
if len(sys.argv) > 2:
    for line in pathlib.Path(sys.argv[2]).read_text(encoding='utf-8').splitlines():
        if '計畫原列 ' in line:
            text = line.split('計畫原列 ', 1)[1].replace('***', '')
            v = json.loads(text if text.startswith('{') else '{' + text + '}')
            OLD[(v['code'], v['category'])] = v
ITEMS = []


def span(start, end):
    s, e = ''.join(start.split()), ''.join(end.split())
    i = RAW.index(s)
    return RAW[i:RAW.index(e, i) + len(e)]


def add(code, name, cat, action, text, quotes, rationale):
    for q in quotes:
        assert ''.join(q.split()) in RAW, (code, q[:30])
    item = dict(code=code, name=name, category=cat, action=action, text=text, quotes=quotes, rationale=rationale)
    if action != 'insert' and OLD:
        row = OLD[(code, cat)]
        if not row['sourceId'].startswith(VIDEO) and not row['sourceId'].startswith('MANUAL-'):
            raise SystemExit(f'{code} 的列不是本支影片產生（{row["sourceId"]}），不在本計畫修改')
        item['previous_source_id'] = row['sourceId']
        item['previous_note_sha256'] = hashlib.sha256(row['note'].encode()).hexdigest()
    ITEMS.append(item)


# 一、身分錯誤：原文沒有這一檔，是日常用語被聽成股名 → 移除
add('2402', '毅嘉', 'holdings', 'delete', '', [span('要攻要9,600億。', '還會在跌回來。')],
    '全文沒有毅嘉；「今天7,200億假的，假的」講的是大盤成交量，「億假」被對成毅嘉。今天沒有毅嘉的持有或操作說法。')
add('3645', '達邁', 'holdings', 'delete', '', [span('9月17號ETF大賣。', '一定要找給你們看，')],
    '全文沒有達邁；「ETF大賣」的「大賣」被對成達邁，那一段講的是9月17日ETF出清世芯-KY。')
# 二、漏收的當下立場（管理者 10/28：明確有錯的都同意）
add('3661', '世芯-KY', 'watch_avoid', 'insert',
    '世芯-KY先前在高檔賣出後股價下跌約1,000元；今天看到股價上漲就追買，仍可能回落到3,600多元。現階段不買進，也不在上漲時追價。',
    [span('我賣的最漂亮，賣完跌1,000塊。', '怎麼樣都會跌回3,600多。'), span('張總，你為什麼今天不買世芯-KY？', '我還不會買，我還不會買。')],
    '「我沒有買世芯-KY」「我不會今天買世芯-KY」「這邊我還不會買」是明講現在不買；開場「全部會員市價買」是講者自己說的謊話示範，不是成交。')
add('2317', '鴻海', 'watch_watch', 'insert',
    '鴻海先前建議在230多元買進，240元以下有大戶承接；股價後續仍會往上，不必緊張。',
    [span('鴻海，再來開鴻海，', '這是鴻海的部分。')],
    '明確的買點區間與向上看法（基準 63 行）；10/06 已賣出、今日沒有成交，不建立交易或持股。')
add('2308', '台達電', 'watch_watch', 'insert',
    '台達電目前在來回震盪打底，已持有者不宜在此殺低，等待打底完成；目前沒有買進台達電。',
    [span('昨天有沒有跟你們講不要亂殺台達電？', '我沒有買台達電，我沒有買台達電。')],
    '對持有者「不要亂殺」並說明震盪打底，是續抱與等待條件（基準 64、179 行）；「我沒有買」只是部位陳述。同 10/27 管理者裁示列觀望注意。')
add('2337', '旺宏', 'watch_avoid', 'insert',
    '旺宏今天宣佈大利多，股價反而下跌；近期宣佈利多的個股多在消息公布後轉跌，不宜因利多消息追進。',
    [span('今天萬紅宣佈大利多，', '不要亂玩，不要亂玩。')],
    '原稿聽寫為「萬紅」，管理者 10/28 確認即旺宏並核准加入別名；原文明講利多後下跌、不要亂玩。')
add('4526', '東台', 'watch_watch', 'insert',
    '東台所屬的工具機族群近期起漲，股價線型沒有出現背離，已持有者可續抱、不必急著賣出；工具機族群後續仍有上漲空間。',
    [span('老師早，4526再抱嗎？', '沒有賣到現在。')],
    '只唸代號 4526；持有的是提問的會員，不是講者部位，管理者 10/28：可列觀望注意、不能列會員持股。')
# 三、說明改寫（分類不變）
add('2344', '華邦電', 'watch_avoid', 'update',
    '華邦電散戶持股過多，不列入注意，也不進場買進。記憶體族群的利空傳聞雖已逐漸消散，仍不會買進華邦電。',
    [span('我不會注意華邦電，', '散戶太多的股票不太想進場。'), span('記憶體是已經鬼故事已經慢慢消失了。', '我也不會去買南亞科。')],
    '原說明「近期宣佈利多反而下跌」講的是旺宏（萬紅），不是華邦電；改為只寫本股的理由。')
add('2408', '南亞科', 'watch_avoid', 'update',
    '南亞科與華邦電同樣散戶持股過多，不列入注意，也不進場買進；記憶體族群利空雖逐漸消散，仍不會買進南亞科。',
    [span('我不會注意華邦電，', '散戶太多的股票不太想進場。'), span('記憶體是已經鬼故事已經慢慢消失了。', '我也不會去買南亞科。')],
    '同華邦電：「宣佈利多反而下跌」是旺宏的事，不借給南亞科。')
add('3533', '嘉澤', 'holdings', 'update',
    '嘉澤昨日上漲130元，今日早盤來到2,000多元；技術指標沒有背離，持股續抱、不賣出，也不再加碼。',
    [span('啊，嘉澤昨天漲130塊，', '可是我不會再買了啊，'), span('你們有看到它背離嗎？', '我幹嘛賣？')],
    '1580、1560、1570元是以前的買進價位，管理者 10/28 傾向從今天的說明移除；保留今日漲幅、續抱與不加碼。')

add('3545', '敦泰', 'watch_watch', 'update',
    '敦泰屬驅動IC族群，先前在40多元時已提及，今日股價創新高，並已突破K線上真正的反壓區；這個位置不是高點，後續會再向上突破。今天不是買進時點，已持有者續抱。',
    [span('我有沒有告訴你驅動IC，除了瑞鼎，', '它後面會向上去突破。'), span('這都不是今天買的哦，', '所以續報，續報，續報。')],
    '紀錄上從未有敦泰的買入或持股，管理者 10/28 裁示列觀望注意、不是會員持股；原說明寫「會員持股」，改為只寫本股的技術位置與續抱建議。')

PLAN = {
    'date': '2026/10/28', 'video_id': VIDEO, 'raw_sha256': RAW_SHA,
    'authorization': '管理者 2026/10/28：明確有錯的都同意修正並更新網站；旺宏（萬紅）加入別名並列觀望不碰、東台列觀望注意不得列會員持股、聖暉不列、嘉澤以前的價位移除；敦泰沒有持股紀錄，列觀望注意不是會員持股（說明不寫會員持股）。不重新寄送通知，不改成交帳本與通知列。',
    'review_basis': '完整閱讀保存原稿（11,277 字）與當日通知，先凍結獨立判讀再比對網站（非盲測：判讀前已看過網站列）。依分類裁決基準 63、64、148、149、179 行逐項核對；刪除僅限原文沒有、由日常用語聽成股名的列。',
    'items': ITEMS,
}
out = ROOT / 'scripts/repairs/2026-10-28-source-review.json'
out.write_text(json.dumps(PLAN, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print('raw', RAW_SHA[:12], 'items', len(ITEMS), 'with previous' if OLD else 'DRAFT (no previous_*)')
for i in ITEMS:
    print(' ', i['action'], i['code'], i['name'], i['category'], len(i['text']))
