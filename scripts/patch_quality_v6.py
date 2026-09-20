"""One-time reviewed source migration. Canonical pipeline first, then sync."""
from pathlib import Path
import re, zipfile
ROOT=Path(__file__).resolve().parents[1]
with zipfile.ZipFile(ROOT/'backups/before-quality-v6.zip','w',zipfile.ZIP_DEFLATED) as z:
    for p in [ROOT/'pipeline/pipeline.py',*list((ROOT/'apps-script').glob('*'))]:
        if p.is_file(): z.write(p,p.relative_to(ROOT))
p=ROOT/'pipeline/pipeline.py'; s=p.read_text(encoding='utf-8')
s=s.replace('只在比喻或舉例裡被點到名的（例如「台積電、鴻海、四星KY、大立光、聯發科都在這一顆球裡面」）不是 holdings，放 ignored。','只在比喻或舉例被點名，不代表持有。回讀它的當下行情與共同指示，依下列觀望規則收錄，不能直接排除。')
s=s.replace('ignored：單純行情例子、法人交易、ETF換股、匿名標的、產業、指數、外國股票。填 name/reason/evidence_refs，保留排除理由供稽核，不塞進個股清單。','ignored：貨幣、產業、指數、匿名標的、外國股票、確無本次原文依據者。台股及已確認 ETF 的行情例子、法人交易、ETF 換股也屬本日觀察範圍，不能因此排除。填 name/reason/evidence_refs，保留排除理由供稽核。')
s=s.replace('只拿來說明盤勢的股票（「這三個月停在這邊沒有動」「昨天漲今天跌」「外資一買一賣」「你去買昨天大漲的就賠錢」）放 ignored，除非講者對它另外給出要買、要等、不要碰的指示。','原文點名的行情或法人例子也逐檔列觀望：追高容易套牢、轉弱、下跌風險或不能承受就不碰→watch_avoid；整理、資金動向、等待機會或未表達偏空的中性觀察→watch_watch。中性者 reason 必須如實寫「僅提及當下行情／資金動向，未提出進場指示」，不可說成推薦買進。ETF 00981A 也收錄，但 ETF 的買賣不能冒充會員買賣；被換股的公司與 ETF 本身分別寫對應事實。')
s=s.replace('price 必須保留數值的用途（成交價、等待買點、缺口、法人成本）。','price 的用途（成交價、等待買點、缺口、法人成本）寫入 reason，不在公開價位欄夾帶文字。多個明確價位以最高數字展示，以上／以下保留；多次買進價不是平均成本。')
s=s.replace('概數、X、以下/以上必須保留，不改成精確成交；','含 X 或無法確認的概數，price 寫未說明並在 reason 忠實描述；以下/以上保留，不改成精確成交；')
s=s.replace('管理者確認：普威、普位、譜位＝譜瑞-KY（4966）','管理者另確認：00981A＝主動統一台股增長 ETF；瑞獄＝瑞昱2379、雨沾＝宇瞻8271、維星＝微星2377、邦店＝華邦電2344、連電＝聯電2303、大力光＝大立光3008、利基電＝力積電6770、宜頂＝宜鼎5289。僅還原本次原文有提及者。管理者確認：普威、普位、譜位＝譜瑞-KY（4966）')
s=s.replace("'初清程': ('8210', '勤誠')}","'初清程': ('8210', '勤誠'),\n    '00981A': ('00981A', '主動統一台股增長'), '主動統一台股增長': ('00981A', '主動統一台股增長'),\n    '瑞獄': ('2379','瑞昱'), '雨沾': ('8271','宇瞻'), '維星': ('2377','微星'),\n    '邦店': ('2344','華邦電'), '連電': ('2303','聯電'), '大力光': ('3008','大立光'),\n    '利基電': ('6770','力積電'), '宜頂': ('5289','宜鼎')}")
# 公司基本資料不含 ETF：只放寬管理者已確認的這個代號，不泛收任意英數字。
s=s.replace(r'\d{4,6}',r'(?:00981A|\d{4,6})')
s=s.replace("review_gaps = gaps or (", "review_gaps = gaps + publication_gaps(signals, transcript) + (")
s=s.replace("gaps = evidence_gaps(repaired, transcript, signals)","gaps = evidence_gaps(repaired, transcript, signals) + publication_gaps(repaired, transcript)")
# 所有名稱與證據問題和文章縮水在既有同一次覆核中處理，沒有固定再加一次 Gemini。
pos=s.index('def audit_context_json(')
s=s[:pos]+'''def publication_gaps(signals, transcript):
    """把可量測的漏收、縮水交給既有全文覆核，不能僅靠 Prompt 的期望字數。"""
    gaps = []
    for row in signals.get('ignored', []):
        name = str(row.get('name') or '')
        if name in NON_EQUITY_NAMES or name in CONFIRMED_INDUSTRY or is_non_stock(name):
            continue
        gaps.append('排除覆核：' + name + ' 若為本次點名台股／ETF，行情、法人、換股亦列觀望；用原文風險／等待條件判方向，不能虛構推薦。純過往交易無現況則 history。')
    if len(re.sub(r'\\s+', '', transcript)) >= 5000:
        market = signals.get('market', [])
        for kind, label, count in ((False,'盤勢',6),(True,'教學',5)):
            rows = [r for r in market if (r.get('kind') == 'view') == kind]
            if len(rows) < count or sum(len(str(r.get('text') or '')) for r in rows) < count * 70:
                gaps.append(label + '內容偏短：重新找全文不同主題，原文充足時至少' + str(count) + '點、每點70–140字；資料不足須在 changes 說明，禁止重複或補造。')
        for cat in SIGNAL_CATEGORIES:
            for row in signals.get(cat, []):
                note = str(row.get('note') if cat == 'holdings' else row.get('reason') or '')
                if len(note) < 70 and sum(len(str(q)) for q in row.get('evidence', [])) >= 200:
                    gaps.append(str(row.get('name')) + '說明偏短：重讀自身相關段落，補出現況、原因、條件與觀察訊號；不借相鄰公司的理由。')
    return gaps


''' +s[pos:]
# 即使沒有日 K，先清理黏連、均線等明顯非價格；不靠乘十湊行情。
s=s.replace('    try:\n        kmap = _daily_k_cached(ss)', '''    for cat in SIGNAL_CATEGORIES:
        for row in signals.get(cat, []):
            value = str(row.get('price') or '')
            formatted = display_price(value, row.get('price_evidence') or '', row.get('reason') or row.get('note') or '')
            if formatted == '未說明' or cat in ('watch_watch','watch_avoid'):
                row['price'] = formatted
    try:
        kmap = _daily_k_cached(ss)''',1)
start=s.index('            if fixed is None:\n                # 補位數之前')
end=s.index('\n            if fixed is not None:', start)
s=s[:start]+s[end:]
# 修復只能使用引用而非模型自己撰寫的說明。
s=s.replace('text_pool = " ".join(str(r.get(f) or "") for f in\n                                 ("reason", "note", "stance"))','text_pool = " ".join(str(q) for q in r.get("evidence", []))')
# 日 K 後段排隊，使用既有 durable checkpoint。普通函式呼叫仍可同步，CLI 明確啟用延後。
s=s.replace('def finish_transcript_refresh(ss, vid, date_str, raw, affected, on_progress=None):','''_DEFER_BACKGROUND = False
_BACKGROUND_REFRESH = []

def drain_background_refresh():
    """文章已落地後才補日 K；中斷保留等待狀態供五分鐘觸發器續跑。"""
    global _DEFER_BACKGROUND
    _DEFER_BACKGROUND = False
    while _BACKGROUND_REFRESH:
        ss, vid, day, raw, affected, progress = _BACKGROUND_REFRESH.pop(0)
        try:
            if progress: progress('文章已更新；背景日K／績效開始，無須重新投稿')
            result = finish_transcript_refresh(ss, vid, day, raw, affected, progress)
            job = globals().get('_CURRENT_JOB')
            if job:
                if result.get('pending'):
                    job_progress(job, step='刷新網站', status='等待日K', note='文章已更新；' + result['note'])
                else:
                    mark_status(ss, vid, day, '後台投稿 '+day, '完成')
                    job_progress(job,step='完成',done=len(ADMIN_STEP_NAMES),total=len(ADMIN_STEP_NAMES),status='完成',note='文章已更新；背景日K與績效已完成；未重寄已寄信件')
        except Exception as exc:
            job = globals().get('_CURRENT_JOB')
            if job: job_progress(job,step='刷新網站',status='等待日K',note='文章已更新；背景更新暫停，將自動續跑：'+str(exc))
            print('背景更新暫停，保留檢查點：'+str(exc))

def finish_transcript_refresh(ss, vid, date_str, raw, affected, on_progress=None):''')
s=s.replace("        result=maybe_refresh_site(only=[name],force=True,date_str=d)",'''        if name == 'perfhist' and _DEFER_BACKGROUND:
            _BACKGROUND_REFRESH.append((ss,vid,date_str,raw,dates,on_progress))
            return {'ok':False,'pending':True,'note':'文章已更新；背景日K／績效將在 Gemini 用量摘要之後執行'}
        result=maybe_refresh_site(only=[name],force=True,date_str=d)''')
s=s.replace('    vid, date_str = job["videoId"], job["date"]','    global _CURRENT_JOB\n    _CURRENT_JOB = job\n    vid, date_str = job["videoId"], job["date"]',1)
s=s.replace('        main()\n        print(gemini_usage_report())','        _DEFER_BACKGROUND = ADMIN_JOB\n        main()\n        print(gemini_usage_report(), flush=True)\n        drain_background_refresh()')
p.write_text(s,encoding='utf-8')
# 精準支援已確認 ETF，避免寫入後被 GAS 補代號步驟再次刪除。
for p in (ROOT/'apps-script').glob('*'):
    if p.suffix not in ('.gs','.html'):continue
    s=p.read_text(encoding='utf-8-sig').replace(r'\d{4,6}',r'(?:00981A|\d{4,6})')
    s=s.replace('2026-09-13-overwrite-v5','2026-09-13-quality-v6')
    p.write_text(s,encoding='utf-8')
print('Applied v6 migration; run sync_quality.py after shared price helper is installed.')
