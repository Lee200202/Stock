"""稽核保存的單日通知原文，以原子操作只替換衍生資料列。

使用既有批次解析器，不重新抓取、不安裝觸發器、不寄送，也不重設通知狀態。通知原文與寄送帳本維持不變。"""
import argparse, hashlib, json, sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from backup_archive import backup_tables
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'pipeline'));import pipeline as pl
def digest(v):return hashlib.sha256(json.dumps(v,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def cells(values):return [{'values':[{'userEnteredValue':{'stringValue':str(x)}} for x in row]} for row in values]
def main():
 p=argparse.ArgumentParser();p.add_argument('--date',required=True);p.add_argument('--apply',action='store_true');a=p.parse_args()
 day=pl.norm_date(a.date);today=datetime.now(ZoneInfo('Asia/Taipei')).date();target=datetime.strptime(day,'%Y/%m/%d').date()
 if not today-timedelta(days=30)<=target<=today:raise SystemExit('日期超出範圍')
 book=pl.open_sheets();ws=book.worksheet('會員簡訊');initial=pl.sheets_retry(ws.get_all_values);head=initial[0]
 required=('文章ID','發文時間','原文','解析明細','解析狀態','解析版本','判定時間')
 if any(x not in head for x in required):raise SystemExit('缺少欄位；未寫入')
 selected=[(i,r,dict(zip(head,r))) for i,r in enumerate(initial[1:],2) if pl.norm_date(dict(zip(head,r)).get('發文時間','').split(' ')[0])==day]
 if not selected:raise SystemExit('沒有該日原文；未寫入')
 ledgers=[s for s in book.worksheets() if s.title in ('寄送帳本','LINE 寄送帳本')]
 before={s.title:digest(pl.sheets_retry(s.get_all_values)) for s in ledgers};sent=pl._daily_article_status(book,day)
 code_map=pl.get_code_map();reports=[];plans=[]
 for num,row,old in selected:
  if old.get('解析版本')==pl.notice_editor().VERSION:
   print('文章',old['文章ID'],'已按本版核對，沿用；0 次模型請求');continue
  r={'id':old['文章ID'],'text':old['原文'],'time':old['發文時間'],'date':day}
  excerpt=pl._sms_transcript_excerpt(book,day,r['text'],code_map)
  items,failed,calls=pl._sms_extract_items(r,pl.CMONEY_MARK_RE,code_map,excerpt)
  if failed:raise SystemExit('解析尚未確認；全部保留原狀')
  items=pl._sms_dedupe_and_condition(items,r,pl.get_prior_sms_buy_prices(book,{'CMONEY-'+r['id']}))
  if not items:raise SystemExit('回傳空清單；人工核對前保留原狀')
  for it in items:
   print('原文核對',it['code'],it['name'],it['action'],it.get('speech_act'))
  # 此儲存庫公開，Actions產物不得包含私人通知、
  # 價位、證據引句或改寫後的私人操作細節。
  reports.append({'article':r['id'],'date':day,'sourceSha256':digest(r['text']),'calls':calls,'items':[{'code':it['code'],'action':it['action'],'speech_act':it.get('speech_act','')} for it in items]})
  plans.append((old,r,items))
 out=ROOT/'.notice-audit';out.mkdir(exist_ok=True);(out/'report.json').write_text(json.dumps(reports,ensure_ascii=False),encoding='utf8')
 if not a.apply or not plans:print('唯讀／沒有待更新項目；未寫入、未寄送');return
 tables=['會員簡訊','操作紀錄','會員持股'];backup_tables(pl,book,tables)
 fresh=pl.sheets_retry(ws.get_all_values)
 for old,r,items in plans:
  current=next((dict(zip(fresh[0],v)) for v in fresh[1:] if dict(zip(fresh[0],v)).get('文章ID')==r['id']),None)
  critical=('文章ID','發文時間','原文','解析明細','解析版本')
  if current is None or any(current.get(k)!=old.get(k) for k in critical):raise SystemExit('核對期間原文或解析結果已改變；未寫入')
 sources={'CMONEY-'+r['id'] for _,r,_ in plans};requests=[]
 for tab,field in [('操作紀錄','理由摘錄'),('會員持股','說明重點')]:
  tw=book.worksheet(tab);vals=pl.sheets_retry(tw.get_all_values);h=vals[0];src=h.index('來源影片ID')
  targets=[n for n,row in enumerate(vals[1:],1) if src<len(row) and row[src] in sources]
  for n in sorted(targets,reverse=True):requests.append({'deleteDimension':{'range':{'sheetId':tw.id,'dimension':'ROWS','startIndex':n,'endIndex':n+1}}})
  appended=[]
  for _,r,items in plans:
   for it in items:
    if (it['action']=='會員持股')!=(tab=='會員持股'):continue
    data={'日期':day,'股票名稱':it['name'],'代號':it['code'],'方向':it['action'],'價位說明':it.get('priceText','未說明'),field:it.get('note',''),'來源影片ID':'CMONEY-'+r['id'],'持股狀態':'續抱'}
    appended.append([data.get(k,'') for k in h])
  if appended:requests.append({'appendCells':{'sheetId':tw.id,'rows':cells(appended),'fields':'userEnteredValue'}})
 for old,r,items in plans:
  n=next(i for i,v in enumerate(fresh[1:],1) if dict(zip(fresh[0],v)).get('文章ID')==r['id'])
  changes={'解析明細':pl.serialize_sms_items(items),'解析狀態':f'已核對 {len(items)} 筆（原文語意）','解析版本':pl.notice_editor().VERSION,'判定時間':pl._sms_now()}
  for key,value in changes.items():requests.append({'updateCells':{'start':{'sheetId':ws.id,'rowIndex':n,'columnIndex':fresh[0].index(key)},'rows':cells([[value]]),'fields':'userEnteredValue'}})
 # 以單一試算表交易寫入，細節與操作列不可只完成一半。
 book.batch_update({'requests':requests})
 checked=pl.sheets_retry(ws.get_all_values)
 for old,r,items in plans:
  current=next(dict(zip(checked[0],v)) for v in checked[1:] if dict(zip(checked[0],v)).get('文章ID')==r['id'])
  assert current['原文']==r['text'] and current['解析明細']==pl.serialize_sms_items(items)
  prior=next(dict(zip(fresh[0],v)) for v in fresh[1:] if dict(zip(fresh[0],v)).get('文章ID')==r['id'])
  for key in ('通知狀態','內容指紋','修訂次數'):assert current.get(key)==prior.get(key)
 assert sent==pl._daily_article_status(book,day)
 assert all(before[s.title]==digest(pl.sheets_retry(s.get_all_values)) for s in ledgers)
 pl.queue_sms_content_sync(book,{day})
 print('原文語意更正已原子寫入；原文、通知狀態及寄送帳本不變；只排內容同步，未寄送')
if __name__=='__main__':main()
