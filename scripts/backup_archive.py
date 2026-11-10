"""在管理者獨立的Google試算表保存經驗證備份，不退回主表。"""
import argparse,json,time,sys
from pathlib import Path
import requests

LOCK_BUSY='備份鎖忙碌'
SOURCE_MOVED='備份核對不符'
sleep=time.sleep

def archive_request(pl,op,names=()):
    if not pl.APPS_SCRIPT_URL or not pl.ADMIN_KEY:
        raise RuntimeError('獨立備份需要 APPS_SCRIPT_URL 與 ADMIN_KEY；未寫入正式資料')
    # 重複快照具有冪等性：備份服務依來源工作表識別碼與輸入摘要索引。
    # 2026/11/06：Apps Script 的備份要拿整個專案的鎖（只等 10 秒），五分鐘排程正在跑時就回「備份鎖忙碌」。
    # 以前這個錯誤不在重試範圍，單日章節修正因此中斷；現在鎖忙碌也重試，間隔漸長，合計約 4 分鐘。
    error=''
    waits=(5,10,15,20,30,40,50,60)
    for attempt in range(len(waits)+1):
        try:
            r=requests.get(pl.APPS_SCRIPT_URL,params={'action':'backup-archive','key':pl.ADMIN_KEY,'source':pl.SPREADSHEET_ID,'op':op,'names':json.dumps(list(names),ensure_ascii=False)},timeout=230,headers={'User-Agent':'stock-backup-archive'})
            data=r.json()
            if not data.get('ok'):
                message=str(data.get('error') or '備份未通過核對')
                # 2026/11/10：快照期間來源分頁被五分鐘排程改到（「備份核對不符：會員簡訊」）也是暫時性的，重試即可；原分頁不受影響。
                if LOCK_BUSY not in message and SOURCE_MOVED not in message:raise RuntimeError(message)
                error=message
            else:
                return data
        except (requests.RequestException,ValueError) as exc:
            error=type(exc).__name__
        if attempt<len(waits):
            print(f'獨立備份第 {attempt+1} 次未完成（{error}），{waits[attempt]} 秒後重試',flush=True)
            sleep(waits[attempt])
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
