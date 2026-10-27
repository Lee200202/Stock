"""套用來源綁定的已核對說明，不重新分類、呼叫模型或寄送。"""
from backup_archive import backup_tables
import argparse,json,hashlib,sys,importlib.util
from pathlib import Path
from datetime import datetime
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'pipeline'));sys.path.insert(0,str(ROOT/'scripts'))
spec=importlib.util.spec_from_file_location('reviewed_notes_pipeline',ROOT/'pipeline/pipeline.py')
pl=importlib.util.module_from_spec(spec);spec.loader.exec_module(pl)
from apply_reviewed_points import source_quote,digest
from gspread.utils import rowcol_to_a1
LABEL={'buy':'買入','sell':'賣出','holdings':'會員持股','watch_watch':'觀望注意','watch_avoid':'觀望不碰'}

def reviewed_items(golden):
    """依代號及分類分開處理說明，剩餘部位不等於買回立場。"""
    out = dict(golden.get('reviewed_notes') or {})
    for item in golden.get('reviewed_note_variants') or []:
        code, cat = str(item.get('code') or ''), item.get('category')
        if not code or cat not in LABEL:
            raise ValueError('分類別說明缺少代號或有效分類')
        key = code + ':' + cat
        if key in out or (code in out and out[code].get('category') == cat):
            raise ValueError('重複的代號與分類說明：' + key)
        out[key] = item
    return out

def note_item(reviewed, code, cat):
    item = reviewed.get(code + ':' + str(cat)) or reviewed.get(code)
    return item if item and item.get('category') == cat else None

def plan_notes(tables,reviewed,day,video_id):
    updates={};matched=set();signals={cat:[] for cat in LABEL};edits=[]
    for tab,values in tables.items():
        head=values[0];field='說明重點' if tab=='會員持股' else '理由摘錄';patches=[]
        for n,cells in enumerate(values[1:],2):
            row=dict(zip(head,cells));code=str(row.get('代號') or '')
            if pl.norm_date(row.get('日期'))!=day:continue
            cat='holdings' if tab=='會員持股' else next((k for k,v in LABEL.items() if v==row.get('方向')),None)
            item=note_item(reviewed,code,cat)
            if not item:continue
            source=str(row.get('來源影片ID') or '')
            if source!=video_id and not pl._is_sms_row(source):continue  # 不覆蓋無關人工補登／其他来源。
            matched.add((code,cat));text=item['text'];name=str(row.get('股票名稱') or '')
            if str(row.get(field) or '')!=text:patches.append({'range':rowcol_to_a1(n,head.index(field)+1),'values':[[text]]})
            signals[cat].append({'code':code,'name':name,'reason':text,'note':text,'evidence':item['quotes'],'_evidence_verified':True})
            if source==video_id:edits.append((tab,name,code,cat,text,source))
        updates[tab]=patches
    return updates,matched,signals,edits

def main():
    p=argparse.ArgumentParser();p.add_argument('--date',required=True);p.add_argument('--apply',action='store_true');a=p.parse_args();day=pl.norm_date(a.date)
    golden=json.loads((ROOT/'scripts/golden'/f"{day.replace('/','-')}.json").read_text(encoding='utf-8'));reviewed=reviewed_items(golden)
    if not reviewed:raise SystemExit('No reviewed notes')
    book=pl.open_sheets();source={};raw,_=pl.existing_transcript(book,'',day,snapshot=source);sha=hashlib.sha256(raw.encode()).hexdigest()
    if sha!=golden.get('reviewed_notes_sha256'):raise SystemExit('原文指紋改變，未寫入')
    for key,item in reviewed.items():
        code=key.split(':',1)[0]
        if item.get('category') not in golden.get('must',{}).get(code,[]) or not item.get('text'):raise SystemExit('分類未經答案核對：'+code)
        item['quotes']=[source_quote(raw,q) for q in item.get('quotes') or []]
        if not item['quotes']:raise SystemExit('缺少原句：'+code)
    tabs=('操作紀錄','會員持股');before={t:book.worksheet(t).get_all_values() for t in tabs}
    updates,matched,signals,manual=plan_notes(before,reviewed,day,source['video_id'])
    additions=[]
    for key,item in reviewed.items():
        code=key.split(':',1)[0]
        if (code,item['category']) in matched:continue
        if not item.get('add_if_missing') or item['category']!='watch_avoid' or not pl.shared_list_refusal({'name':golden['names'][code]},raw):raise SystemExit('沒有匹配列，未寫入：'+code)
        cm=pl.get_code_map()
        if pl._display_name(cm.get(code))!=pl._display_name(golden['names'][code]):raise SystemExit('官方名稱與代號不符：'+code)
        row={'日期':day,'股票名稱':golden['names'][code],'代號':code,'方向':LABEL[item['category']],'價位說明':'未說明','理由摘錄':item['text'],'來源影片ID':source['video_id'],'序':1}
        additions.append([row.get(h,'') for h in before['操作紀錄'][0]]);manual.append(('操作紀錄',row['股票名稱'],code,item['category'],item['text'],source['video_id']))
        signals[item['category']].append({'code':code,'name':row['股票名稱'],'reason':item['text'],'evidence':item['quotes'],'_evidence_verified':True})
    # 來源修復不是模型獨立通過：原稿指紋及逐字引句已在上面驗證，另綁定審核者確認的本股主體。
    # 長篇基本面分析可能超出名稱附近窗口，不能因此把原有年度／半年時序刪去。
    for cat,rows in signals.items():
        for row in rows:
            item=note_item(reviewed,str(row['code']),cat)
            if item and item.get('subject_reviewed') is True:
                row['_reviewed_note_context']={'code':str(row['code']),'subject_reviewed':True,
                    'source_sha256':sha,'text_sha256':hashlib.sha256(row['note'].encode()).hexdigest(),
                    'quotes':item['quotes']}
    overview,hard,_=pl.quality_overview(signals,raw,check_chapters=False)
    for line in overview:
        if not line.startswith(('模型直接產出','盤勢 ')):print('品質概況：'+line)
    print('說明來源：逐句人工核對的書面整理；本次不重新生成、不修改盤勢與教學章節')
    if hard:raise SystemExit('已核對說明仍有硬傷，未寫入：'+'；'.join(hard))
    count=sum(len(v) for v in updates.values());print('原句核對通過；更新說明',count,'筆，新增明確共同否定',len(additions),'筆；0模型請求')
    if not a.apply:return
    ledger_names=[w.title for w in book.worksheets() if w.title in ('寄送帳本','LINE 寄送帳本')]
    ledgers={n:digest(book.worksheet(n).get_all_values()) for n in ledger_names};sent=pl._daily_article_status(book,day)
    stamp=datetime.now(pl.TAIPEI).strftime('%m%d_%H%M%S')
    backup_tables(pl,book,tabs+('每日推播內容',))
    if any(digest(before[t])!=digest(book.worksheet(t).get_all_values()) for t in tabs):raise SystemExit('備份期間資料已變，未寫入')
    fresh={};latest,_=pl.existing_transcript(book,source['video_id'],day,snapshot=fresh)
    if hashlib.sha256(latest.encode()).hexdigest()!=sha:raise SystemExit('備份期間原文已變，未寫入')
    for t,changes in updates.items():
        if changes:pl.sheets_retry(book.worksheet(t).batch_update,changes,value_input_option='RAW')
    if additions:pl.sheets_retry(book.worksheet('操作紀錄').append_rows,additions,value_input_option='RAW')
    now=datetime.now(pl.TAIPEI).strftime('%Y/%m/%d %H:%M:%S');log=[]
    for tab,name,code,cat,text,vid in manual:
        meta={'v':1,'sourceId':vid,'sourceSha256':sha,'category':cat,'note':text}
        log.append([now,day,'保留人工說明',tab,name,code,json.dumps(meta,ensure_ascii=False),'已套用（人工說明）',now])
    if log:pl.sheets_retry(book.worksheet('修正建議').append_rows,log,value_input_option='RAW')
    # 分類、來源、價格及其他日期逐欄不動；同時確認實際讀回每一則修正。
    after={t:book.worksheet(t).get_all_values() for t in tabs}
    for t in tabs:
        note_col=before[t][0].index('說明重點' if t=='會員持股' else '理由摘錄')
        for old,new in zip(before[t][1:],after[t][1:]):
            if old[:note_col]+old[note_col+1:]!=new[:note_col]+new[note_col+1:]:raise RuntimeError('非說明欄位變動，請核對備份')
    remaining,_,_,_=plan_notes(after,reviewed,day,source['video_id'])
    if any(remaining.values()):raise RuntimeError('寫入讀回不一致')
    r=pl.maybe_refresh_site(force=True,only=['smsmail','tracker','perf'],date_str=day) or {}
    if not r.get('ok'):raise RuntimeError('說明已保存，請只續跑網站刷新，不重判或重寫')
    if sent!=pl._daily_article_status(book,day) or any(ledgers[n]!=digest(book.worksheet(n).get_all_values()) for n in ledgers):raise RuntimeError('寄送狀態或帳本有其他執行寫入，需核對')
    print('正式說明已讀回；寄送狀態與帳本未變；網站刷新完成；無重新取稿、判讀或寄送')
if __name__=='__main__':main()
