"""Read the authoritative saved YouTube transcript; no model, writes, or notifications."""
import argparse,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'pipeline'));import pipeline as pl
p=argparse.ArgumentParser();p.add_argument('--date',required=True);a=p.parse_args();day=pl.norm_date(a.date)
ss=pl.open_sheets();snapshot={};raw,polished=pl.existing_transcript(ss,'',day,snapshot=snapshot)
if not raw or not snapshot.get('video_id'):raise SystemExit('Source unavailable')
out=ROOT/'.source-audit';out.mkdir(exist_ok=True)
report={'date':day,'videoId':snapshot['video_id'],'rawSha256':hashlib.sha256(raw.encode()).hexdigest(),'raw':raw,'polished':polished,'readOnly':True}
(out/'source.json').write_text(json.dumps(report,ensure_ascii=False),encoding='utf-8')
print('唯讀來源核對',day,'影片',snapshot['video_id'],'原文',len(raw),'字；不呼叫模型、不更新資料、不寄送')
