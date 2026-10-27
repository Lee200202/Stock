"""2026/10/27 來源核對修正計畫：依完整原稿、當日通知與管理者 10/27 裁示產生；每段引句須逐字存在於保存原稿。"""
import hashlib
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = json.load(open(r'C:/Users/user/Stock/scratch/ai-handoff-20261027-today-ph2/source-context-1027.json', encoding='utf-8'))
RAW = SRC['raw']
assert hashlib.sha256(RAW.encode()).hexdigest() == SRC['rawSha256']
LOG = pathlib.Path(r'C:/Users/user/Stock/scratch/worktrees/1027-source-review/scratch/inspect-1027.txt').read_text(encoding='utf-8')
OLD = {}
for line in LOG.splitlines():
    if '計畫原列 ' in line:
        text = line.split('計畫原列 ', 1)[1].replace('***', '')
        v = json.loads(text if text.startswith('{') else '{' + text + '}')
        OLD[(v['code'], v['category'])] = v


def span(start, end, include_end=True):
    a = RAW.index(start)
    b = RAW.index(end, a)
    return RAW[a:b + (len(end) if include_end else 0)]


ITEMS = []


def add(code, name, cat, action, text, quotes, rationale, previous=None):
    for q in quotes:
        assert q in RAW, (code, q[:30])
    item = dict(code=code, name=name, category=cat, action=action, text=text, quotes=quotes, rationale=rationale)
    if action != 'insert':
        row = OLD[(code, previous or cat)]
        item['previous_source_id'] = row['sourceId']
        item['previous_note_sha256'] = hashlib.sha256(row['note'].encode()).hexdigest()
        if previous and previous != cat:
            item['previous_category'] = previous
    ITEMS.append(item)


MATURE = span('真正的成熟製程，', '我只有 力積電。')
MEMORY = span('記憶體我不是在看華邦電、南亞科。', '宜鼎。')
MEMORY_TAIL = span('這兩隻 目前', '我有 力積電就好。')

# 一、只是部位或關注範圍的陳述 → 不列（分類裁決基準 148 行）
add('2303', '聯電', 'watch_avoid', 'delete', '', [MATURE],
    '「我沒有聯電」只說明講者部位，原文沒有聯電本股的負面看法或禁買；不列。')
add('5347', '世界', 'watch_avoid', 'delete', '', [MATURE],
    '「我沒有世界先進」只說明講者部位；不列。')
add('2408', '南亞科', 'watch_avoid', 'delete', '', [MEMORY],
    '「記憶體我不是在看華邦電、南亞科」是關注範圍；「還沒有穩」講的是群聯、宜鼎，不能移給南亞科。')
# 二、匿名段落錯配 → 移除
CHENG = span('這一隻股票，好好把握， 叫做勤誠。', '3558神準， 本來也是我的股票。')
add('2402', '毅嘉', 'holdings', 'delete', '', [CHENG],
    '全文與當日通知都沒有毅嘉；成交量、外資買進、假破底、超級便宜都在勤誠段落內（勤誠 10/05、10/13 已買入，「這個本來就是我的股票」接在勤誠之後），不是毅嘉。')
# 三、管理者 10/27 裁示：台達電、群聯、宜鼎 → 觀望注意
add('2308', '台達電', 'watch_watch', 'update',
    '台達電今天跌破近期新低，也已跌破年線，短線屬於超跌。已持有者不宜在低檔恐慌殺低，耐心等待反彈再評估；現階段不必急著加碼，本身也沒有買進計畫。',
    [span('台達電今天破新低。', '雖然我沒有台達電。')],
    '對已持有者的建議是勿殺低、等反彈，「不准殺低」不是禁止買進（基準 64、179 行）；本人沒有要買屬部位陳述。管理者 10/27 裁示列觀望注意。',
    previous='watch_avoid')
add('8299', '群聯', 'watch_watch', 'update',
    '群聯是記憶體族群中主要觀察的兩檔之一，但目前走勢還沒有止穩、上攻速度也還不快，暫時沒有出手計畫，不必急著進場，等待止穩訊號再評估。',
    [MEMORY, MEMORY_TAIL],
    '「主要是看這兩隻…還沒有穩…不用急」是等待止穩的觀察條件，沒有明講現在不能買（基準 149 行）；管理者 10/27 裁示列觀望注意。',
    previous='watch_avoid')
add('5289', '宜鼎', 'watch_watch', 'update',
    '宜鼎與群聯同列記憶體族群的主要觀察標的，目前同樣尚未止穩，暫不急於進場，等待走勢穩定後再評估。',
    [MEMORY, MEMORY_TAIL],
    '同群聯；原稿「宜蘭之光」後文直接寫明「宜鼎」。管理者 10/27 裁示列觀望注意。',
    previous='watch_avoid')
# 四、漏收
add('3008', '大立光', 'watch_avoid', 'insert',
    '大立光現階段看不到上漲動能，與聯發科同屬目前不買進的標的。',
    [span('為什麼張震不讓你買聯發科跟大立光？', '你們買他幹什麼？')],
    '與聯發科同一句明講不讓買、「現在又沒有要漲」；原文對大立光只有這一句，說明不借聯發科的缺口與跌幅。')
add('2317', '鴻海', 'watch_watch', 'insert',
    '鴻海維持區間操作：230多元附近是可以買進的位置，漲到250元以上則不追價。',
    [span('第二個鴻海，鴻海就不用我講了', '這就是鴻海的走法，對不對？')],
    '明確的買點與不追價上緣（基準 63 行可辨認的買點條件）；10/06 已賣出、今日沒有成交，不建立交易或持股。')
add('3037', '欣興', 'watch_avoid', 'insert',
    '欣興今天的利多新聞多達8則，外資與大摩也調高目標價至1,700多元，股價卻轉為下跌；利多消息集中時反而出現賣壓，有利多出貨的疑慮，宜小心、暫不追進。',
    [span('所有新聞今天都在推薦一隻股票。', '新新有底氣，大摩暗棧，結果跌。')],
    '原稿聽寫為「新新」；管理者 10/27 確認即欣興。原文明講利多出貨、小心。新新尚未加入名稱別名。')
# 五、說明改寫（分類不變）
add('2327', '國巨*', 'watch_avoid', 'update',
    '國巨過去三個月都不在建議買進之列。颱風假前一天股價跌到507元，上週四單日下跌35元，今天僅反彈十多元，弱勢尚未扭轉，仍不宜進場。',
    [span('被動元件哪一隻股票絕對不能碰？', '我辭職下台，對不對？')],
    '507元是假期前的價位，原說明寫成今日現況；改依原稿時間順序，去除「明確不宜碰觸的標的」等標籤。')
add('2454', '聯發科', 'watch_avoid', 'update',
    '聯發科下方缺口仍未補，媒體報導指出不到一個月下跌約24%，跌勢持續；現階段不買進，已持有者也不宜在下跌中逆勢加碼。',
    [span('鴻海，我有沒有跟你講，我絕對不買聯發科。', '錯上加錯。')],
    '去除「被點名為…」「看不懂股票的人」等轉述過程與情緒字眼，保留缺口、跌幅與不加碼。')
add('3665', '貿聯-KY', 'watch_avoid', 'update',
    '貿聯-KY自2,685元跌至1,750元附近，跌幅接近千元，股價已落後同族群的嘉澤逾200元；即使外資調高目標價，股價仍走弱，現階段不碰。',
    [span('茂聯-KY跌多少？', '2,685 跌到 1,750 塊，有沒有跌了快 1,000 塊？'),
     span('不可能！ 茂林外資調高目標價', '絕對不要碰茂林。')],
    '原說明「買進將面臨明顯虧損」把風險寫成必然結果、「強調」是轉述過程；改為書面事實。')
add('2049', '上銀', 'watch_watch', 'update',
    '上銀上週仍在下跌，今天是第一天上漲並攻上漲停，接下來有機會往季線靠近；工具機族群在新台幣匯率變動後可望走一波，可留意族群後續表現。',
    [span('他的同類 叫做上銀', '上銀上個禮拜還在跌，不關我的事。'),
     span('我說工具機， 這一次要漲一波。', '這個話往季線去走。')],
    '原說明後半「若真的很想買，最適合買低檔假破底的股票」是下一段勤誠的內容，不屬於上銀。')
add('2330', '台積電', 'holdings', 'update',
    '台積電先前在1,820、2,025、2,135元買進的部位續抱。目前股價在2,400元附近，短線空間有限，新資金先不追買，保留現金準備買進其他標的。',
    [span('台積電該買的時候是 1820、2025、2135，', '所以台積電就就手中有的就抱著。')],
    '合併原說明中重複兩次的「新會員不要追買」；既有部位與不追新買分開寫。')
add('6770', '力積電', 'holdings', 'update',
    '力積電是記憶體族群中唯一持有的標的，目前仍小幅套牢、續抱。上週四大盤重挫800多點時股價逆勢上漲兩毛，今天也上漲3元多，顯示抗跌且有買盤，看好後續走高。',
    [span('記憶體裡面，', '是有買盤的。'), span('你們不是要聽人家講，我跟你講我有力積電啦。', '我還小套啦。')],
    '消除首尾重複的「小套」，補上今日上漲3元多。')

PLAN = {
    'date': SRC['date'], 'video_id': SRC['videoId'], 'raw_sha256': SRC['rawSha256'],
    'authorization': '管理者 2026/10/27 要求核對另一個 AI 的建議、自行判斷今日分類並更新今日網站；台達電、群聯、宜鼎列觀望注意與「新新」即欣興為管理者當日裁示。不重新寄送通知，不改成交帳本與通知列。',
    'review_basis': '完整閱讀保存原稿（12,603 字）與當日通知原文，先凍結獨立判讀再看流程與網站（非盲測：另一個 AI 的表已先讀過）。依分類裁決基準 63、64、148、149、179 行逐項核對；刪除僅限當日誤收或錯配的提及，不否認以往真實部位。',
    'items': ITEMS,
}
out = ROOT / 'scripts/repairs/2026-10-27-source-review.json'
out.write_text(json.dumps(PLAN, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print('items', len(ITEMS), {a: sum(i['action'] == a for i in ITEMS) for a in ('update', 'delete', 'insert')})
for i in ITEMS:
    print(' ', i['action'], i['code'], i['name'], i.get('previous_category', ''), '→', i['category'], len(i['text']))
