"""Verified backups in the owner's separate Google spreadsheet. Never falls back to main."""
import argparse,json,time,sys
from pathlib import Path
import requests

def archive_request(pl,op,names=()):
    if not pl.APPS_SCRIPT_URL or not pl.ADMIN_KEY:
        raise RuntimeError('獨立備份需要 APPS_SCRIPT_URL 與 ADMIN_KEY；未寫入正式資料')
    # Repeating snapshot is idempotent: the owner indexes source sheet ID + input digest.
    error=''
    for attempt in range(3):
        try:
            r=requests.get(pl.APPS_SCRIPT_URL,params={'action':'backup-archive','key':pl.ADMIN_KEY,'source':pl.SPREADSHEET_ID,'op':op,'names':json.dumps(list(names),ensure_ascii=False)},timeout=230,headers={'User-Agent':'stock-backup-archive'})
            data=r.json()
            if not data.get('ok'):raise RuntimeError(data.get('error') or '備份未通過核對')
            return data
        except (requests.RequestException,ValueError) as exc:
            error=type(exc).__name__
            if attempt<2:time.sleep(5*(attempt+1))
    raise RuntimeError('備份回應未能確認；正式寫入已停止：'+error)

def backup_tables(pl,book,names):
    result=archive_request(pl,'snapshot',names)
    if result.get('sourceId')!=book.id or len(result.get('verified') or [])!=len(names):
        raise RuntimeError('備份來源或分頁數不符；未寫入正式資料')
    print('獨立備份已核對：'+str(len(names))+' 張工作表')
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument('--migrate',action='store_true');a=p.parse_args()
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pipeline'));import pipeline as pl
    op='migrate' if a.migrate else 'status'
    for _ in range(40):
        result=archive_request(pl,op)
        print(json.dumps(result,ensure_ascii=False))
        if not a.migrate or result.get('done'):return
        if result.get('errors') or not result.get('verified'):raise RuntimeError('搬移尚未完成；原分頁保留，須核對上述原因')
    raise RuntimeError('本輪已到批次上限；可安全接續搬移，原分頁未提前移除')
if __name__=='__main__':main()
