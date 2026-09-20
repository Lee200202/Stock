"""Incremental migration from current v9; never rerun an older full-file patch."""
from pathlib import Path
import zipfile
ROOT=Path(__file__).resolve().parents[1]
with zipfile.ZipFile(ROOT/'backups/before-semantics-v10.zip','w',zipfile.ZIP_DEFLATED) as z:
    for p in [ROOT/'AGENTS.md',ROOT/'pipeline/pipeline.py',*list((ROOT/'apps-script').glob('*'))]:
        if p.is_file():z.write(p,p.relative_to(ROOT))
p=ROOT/'pipeline/pipeline.py';s=p.read_text(encoding='utf-8')
s=s.replace('(?:張震|張正)(?:老師)?', '(?:張震|張正|講者)(?:老師)?')
# 不增加大篇幅日期答案；用當次已確認的主詞關係示範同一條通用規則。
marker='【主詞與分類】'
addition='''【主詞歸屬與語氣】
逐句分清交易者、被買賣的標的、建議適用對象。ETF交易個股時，ETF是交易者，不能承接個股買點。price_subject填該價位所屬的原文名稱；不明則price未說明。
例如ETF出清「出金城／初清程」且等破900再買：900屬勤誠，不屬00981A；ETF本身只寫其換股評價。普位現在跌兩塊屬譜瑞；昨日跌百餘元買進、今日漲30幾塊屬四星KY，不能互搬。原文沒這些話時不加入範例。
觀望注意須有本股明確正面看法或具體等待買點；只有中性、法人反覆換手、追高風險且沒有正面指示，列觀望不碰。不是永久看壞公司。明確低檔買點不因「不要追高」改判偏空；正面後明講禁買則不碰。純已出清回顧不因此復活。
會員已買而仍等待整理者保留持股，不被末段候選名單抹掉。觀望兩類衝突先核對適用對象、最後有效指示，不採關鍵字偏空一律優先。
'''
s=s.replace(marker,addition+'\n'+marker,1)
# 把旧中性默认看多的冲突文字一并删除；保留其余 v9 提示与503锁。
s=s.replace('觀望注意只收正面或無負面評語的中性觀察','觀望注意只收明確正面看法或具體等待買點')
s=s.replace('正面或沒有負面評語的中性觀察','明確正面看法或具體等待買點')
s=s.replace('正面或中性','明確正面')
s=s.replace('中性者 reason 必須如實寫「僅提及當下行情／資金動向，未提出進場指示」','無正面指示的中性者列 watch_avoid，reason 如實寫當下行情／資金動向')
s=s.replace('不寫人名當主詞（不寫張震指出、張正提及）','不寫人名或「講者」當主詞（不寫張震指出、張正提及、講者表示）')
s=s.replace('或直接寫事實。','或直接寫事實；禁止用「講者」替代被移除的人名。',1)
# 明講股名而模型補了一個原文完全沒出現的代號，不可再讓那個代號干擾名稱。
needle='            # 名稱與代號指向不同檔時，先仲裁再比對。'
s=s.replace(needle,"            if hint and transcript and not re.search(r'(?<![A-Za-z0-9])' + re.escape(hint) + r'(?![A-Za-z0-9])', transcript):\n                r['未採用模型代號'] = hint\n                hint = ''\n"+needle)
# 修正最後一次自然化的時機：日期轉類與沿用舊資料也可能帶回錯字。
s=s.replace('    signals = ensure_min_lessons(signals, TX["audit"], date_str)', '    signals = ensure_min_lessons(signals, TX["audit"], date_str)\n    signals = normalize_watch_tones(signals)\n    signals = sanitize_entity_claims(signals, TX["audit"])\n    signals = naturalize_signal_reasons(signals)')
s=s.replace('    gaps = evidence_gaps(signals, transcript)\n    # A valid quote', '    gaps = evidence_gaps(signals, transcript) + entity_claim_gaps(signals, transcript)\n    # A valid quote',1)
s=s.replace('gaps = evidence_gaps(repaired, transcript, signals) + publication_gaps(repaired, transcript)', 'gaps = evidence_gaps(repaired, transcript, signals) + publication_gaps(repaired, transcript) + entity_claim_gaps(repaired, transcript)')
# 新欄位是語意資訊，不能以空白外推身分；只用於發現衝突。
s=s.replace("'宜頂': ('5289','宜鼎')", "'宜頂': ('5289','宜鼎')")
s=s.replace("'移頂': ('5289','宜鼎'), '以頂': ('5289','宜鼎')}", "'移頂': ('5289','宜鼎'), '以頂': ('5289','宜鼎'),\n    '木德': ('3563','牧德'), '四星KY': ('3661','世芯-KY'), '四星': ('3661','世芯-KY'),\n    '宏準': ('2354','鴻準'), '弘準': ('2354','鴻準'), '紅準': ('2354','鴻準'), '威星': ('2377','微星')}")
# 只有反覆交易的法人故事不再落回「自己的歷史成交」而消失。
s=s.replace("    if not _has_directive(view):", "    if not _has_directive(view) and not third_party_churn(view):",1)
s=s.replace("        view, quotes = _current_view(r, segments)\n        fact", "        if not r.get('view') and third_party_churn(str(r.get('reason') or '')):\n            r['view'] = r['reason']; r['view_evidence'] = r.get('evidence') or []\n        view, quotes = _current_view(r, segments)\n        fact",1)
s=s.replace("    if b in WATCH_BIAS_LABEL:\n        return b\n    return sentiment_of(view)", "    # 模型的 watch_bias 不能把沒有偏多依據的法人換手故事變成看多。\n    return watch_tone(view) if view else (b if b in WATCH_BIAS_LABEL else 'watch_avoid')",1)
# API 兼容名稱保持；所有觀望最後共用語氣判準。
start=s.index('def sentiment_of(reason: str) -> str:');end=s.index('\n\n# 需要被重新歸類',start)
s=s[:start]+'''def sentiment_of(reason: str) -> str:
    """觀望注意須有正面依據；中性保留為風險觀察，不捏造買點。"""
    return watch_tone(reason)
'''+s[end:]
p.write_text(s,encoding='utf-8')
p=ROOT/'apps-script/Config.gs';p.write_text(p.read_text(encoding='utf-8').replace('2026-09-13-quality-v9','2026-09-13-quality-v10'),encoding='utf-8')
p=ROOT/'apps-script/Adminservice.gs';s=p.read_text(encoding='utf-8').replace('(?:張震|張正)(?:老師)?','(?:張震|張正|講者)(?:老師)?').replace('正面或無負面評語的中性觀察','明確正面看法或具體等待買點').replace('正面或中性','明確正面');p.write_text(s,encoding='utf-8')
print('v9 preserved; applied incremental entity/subject policy changes.')
