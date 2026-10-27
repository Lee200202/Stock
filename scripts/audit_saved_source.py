"""讀取正式保存的YouTube逐字稿，不呼叫模型、寫入或通知。"""
import argparse,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'pipeline'));import pipeline as pl
p=argparse.ArgumentParser();p.add_argument('--date',required=True);p.add_argument('--diagnostic-context',action='store_true');a=p.parse_args();day=pl.norm_date(a.date)
ss=pl.open_sheets();snapshot={};raw,polished=pl.existing_transcript(ss,'',day,snapshot=snapshot)
if not raw or not snapshot.get('video_id'):raise SystemExit('Source unavailable')
out=ROOT/'.source-audit';out.mkdir(exist_ok=True)
report={'date':day,'videoId':snapshot['video_id'],'rawSha256':hashlib.sha256(raw.encode()).hexdigest(),'raw':raw,'polished':polished,'readOnly':True}
if a.diagnostic_context:
    import re
    from datetime import datetime
    def scrub(value):
        text=str(value or '')
        text=re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[EMAIL遮蔽]', text)
        text=re.sub(r'(?<!\d)(?:\+?886[- ]?|0)(?:9\d{2}|2|3|4|5|6|7|8)[- ]?\d{3,4}[- ]?\d{3,4}(?!\d)', '[電話遮蔽]', text)
        return text
    def rows(title):
        values=pl.sheets_retry(ss.worksheet(title).get_all_values)
        return [dict(zip(values[0], r)) for r in values[1:]] if values else []
    def select(row, keys):
        return {k:scrub(row[k]) for k in keys if k in row}
    notices=[]
    for row in rows('會員簡訊'):
        if pl.norm_date(str(row.get('發文時間') or '')[:10]) != day:continue
        selected=select(row, ('文章ID','發文時間','標題','原文','解析狀態','解析明細','解析版本','判定時間','收錄個股'))
        selected['original_text_sha256']=hashlib.sha256(str(row.get('原文') or '').encode()).hexdigest()
        selected['contact_redacted']=selected.get('原文','') != str(row.get('原文') or '')
        notices.append(selected)
    operations=rows('操作紀錄')
    fields=('日期','股票名稱','名稱','代號','方向','參考價位','價格','價位','理由摘錄','來源影片ID','來源','更新時間')
    before=[select(r,fields) for r in operations if pl.norm_date(r.get('日期')) and pl.norm_date(r['日期'])<day
            and pl._dir_kind(str(r.get('方向') or '')) in ('buy','sell')]
    today=[select(r,fields) for r in operations if pl.norm_date(r.get('日期'))==day]
    holding_fields=('日期','股票名稱','名稱','代號','說明','持股說明','來源影片ID')
    holdings=[select(r,holding_fields) for r in rows('會員持股') if pl.norm_date(r.get('日期')) and pl.norm_date(r['日期'])<day]
    report['diagnostic_context']={'captured_at':datetime.now(pl.TAIPEI).isoformat(timespec='seconds'),
        'target_day_notice_originals_and_saved_parses':notices,'pre_date_buy_sell_operations':before,
        'pre_date_holding_mentions':holdings,'target_day_operations_for_later_comparison':today,
        'scope':'只讀指定日通知及該日前操作／持股提及；不讀訂閱名單、聯絡人或寄送帳本，不呼叫模型或寫表。',
        'limitations':'既有解析與部位是可稽核資料，不是人工核定真值；今日影片產生的分類只供後讀。聯絡資訊遮蔽。'}
(out/'source.json').write_text(json.dumps(report,ensure_ascii=False),encoding='utf-8')
print('唯讀來源核對',day,'影片',snapshot['video_id'],'原文',len(raw),'字；不呼叫模型、不更新資料、不寄送')
