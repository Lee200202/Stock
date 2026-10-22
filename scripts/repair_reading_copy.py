"""預覽或套用已保存的單日閱讀稿，保留原始證據與寄送帳本。"""
import argparse, hashlib, json, sys
from pathlib import Path
from backup_archive import backup_tables
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'pipeline'))
import pipeline as pl

def main():
    p=argparse.ArgumentParser();p.add_argument('--date',required=True);p.add_argument('--apply',action='store_true');a=p.parse_args()
    day=pl.norm_date(a.date);ss=pl.open_sheets();snapshot={}
    raw,draft=pl.existing_transcript(ss,'',day,snapshot=snapshot)
    if not raw or not snapshot.get('video_id'):raise SystemExit('來源尚未就緒；沒有寫入')
    ws=ss.worksheet('影片清單');_,row=pl.select_transcript_row(pl.sheets_retry(ws.get_all_records),'',day)
    if pl.reading_editor().verified_reading(row,pl.transcript_fingerprint):
        print('原稿指紋相同，已核對校正版沿用；0 次模型請求');return
    base=draft if pl.reading_editor().words(draft)==pl.reading_editor().words(raw) else pl.format_readable_transcript(raw)
    updated,proof=pl.correct_reading_copy(raw,base)
    folder=ROOT/'.reading-audit';folder.mkdir(exist_ok=True)
    (folder/'report.json').write_text(json.dumps({'date':day,'videoId':snapshot['video_id'],'rawSha256':snapshot['sha256'],'proof':proof,'updated':updated},ensure_ascii=False),encoding='utf8')
    if not a.apply or not proof:
        print('唯讀預覽／沒有可採用校正；沒有改寫或寄送');return
    ledgers=[w for w in ss.worksheets() if w.title in ('寄送帳本','LINE 寄送帳本')]
    digest=lambda v:hashlib.sha256(json.dumps(v,ensure_ascii=False).encode()).hexdigest()
    before={w.title:digest(pl.sheets_retry(w.get_all_values)) for w in ledgers}
    backup_tables(pl,ss,['影片清單'])
    _,current=pl.select_transcript_row(pl.sheets_retry(ws.get_all_records),'',day)
    if str(current.get('原始逐字稿內容') or '')!=str(row.get('原始逐字稿內容') or '') or str(current.get('修飾後逐字稿內容') or '')!=str(row.get('修飾後逐字稿內容') or ''):
        raise SystemExit('校對期間來源或修飾稿改變；沒有寫入')
    pl.write_transcripts(ss,snapshot['video_id'],raw,updated,day,snapshot=snapshot,reading_proof=proof)
    _,check=pl.select_transcript_row(pl.sheets_retry(ws.get_all_records),'',day)
    assert pl.reading_editor().verified_reading(check,pl.transcript_fingerprint)
    assert str(check.get('原始逐字稿內容') or '')==str(row.get('原始逐字稿內容') or '')
    assert all(before[w.title]==digest(pl.sheets_retry(w.get_all_values)) for w in ledgers)
    pl.ensure_transcript_layout(ss,day,force=True)
    print('閱讀校正版已寫入並重建版面；原稿與寄送帳本一致，沒有重新判讀或寄送')
if __name__=='__main__':main()
