"""Fresh full-video acceptance using production clients; never writes production sheets.

Requires the same environment as transcript.py. Output is a short-lived Actions artifact.
Run: python scripts/verify_transcript_live.py --date YYYY/MM/DD
"""
import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import transcript as t


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=t.parse_date, required=True)
    parser.add_argument("--audio-only", action="store_true", help="備援模型獨立聽打三段重點，不改正式資料")
    parser.add_argument("--segment-minutes", type=int, choices=(15, 30), default=t.SEGMENT_MINUTES)
    args = parser.parse_args()
    started = time.monotonic()
    v = t.find_video(args.date, require_details=True)
    if not v or not v.is_ready(datetime.now(t.TAIPEI)):
        raise RuntimeError("目標影片尚未可轉錄，驗收不繞過直播／回放保護")
    ss = t.open_sheets()
    vocabulary = t.load_day_vocabulary(ss, args.date.strftime("%Y/%m/%d"))
    _, _, _, row = t.read_state(ss, "", args.date.strftime("%Y/%m/%d"))
    original = str(row.get(t.COL_RAW) or "")
    keys = t.gemini_keys()
    if not keys:
        raise RuntimeError("缺少 Gemini 金鑰")
    out = Path(os.environ.get("TRANSCRIPT_VERIFY_OUTPUT", ".transcript-verification"))
    out.mkdir(parents=True, exist_ok=True)
    t.log(f"實際驗收：{v.url}，片長 {t.hms(v.duration_sec)}，通道 generateContent；不寫正式資料")
    if args.audio_only:
        # Independent text from a different model; do not feed it the candidate transcript.
        model = t.GEMINI_FALLBACK_MODEL or t.GEMINI_MODEL
        tr = t.Transcriber(keys, model=model, fallback=[])
        samples = []
        for start, end in ((0, 180), (1560, 2040), (2220, 2460)):
            if start >= v.duration_sec:
                continue
            end = min(end, v.duration_sec)
            text, complete = tr._request(v.url, start, end)
            if not complete:
                raise RuntimeError("抽段回聽輸出截斷，不能當作核對證據")
            samples.append({"start_sec": start, "end_sec": end, "text": text})
            (out / f"audio-{start}-{end}.txt").write_text(text, encoding="utf-8")
        report = {"verified_at_taipei": datetime.now(t.TAIPEI).isoformat(timespec="seconds"),
                  "video_id": v.id, "model": model, "samples": samples,
                  "elapsed_sec": round(time.monotonic() - started, 2),
                  "production_sheet_writes": 0, "email_sends": 0, "line_sends": 0,
                  "limitations": "不同模型直接聽同一影片的抽段證據；不等於人工全片校聽。"}
        (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"獨立抽段完成：{len(samples)} 段，模型 {model}，{report['elapsed_sec']} 秒")
        return 0
    # Every segment starts empty: deliberately do not reuse today's production cache.
    cache = t.SegmentCache(out / "segments.json")
    cache.clear()
    tr = t.Transcriber(keys, segment_minutes=args.segment_minutes)
    text = tr.transcribe(v.url, v.duration_sec, cache=cache)
    elapsed = time.monotonic() - started
    (out / "transcript.txt").write_text(text, encoding="utf-8")
    (out / "production-original.txt").write_text(original, encoding="utf-8")
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if len(p.strip()) >= 60]
    repeats = sorted({p for p in paragraphs if paragraphs.count(p) > 1})
    cleaned, echoes = t.strip_transcribe_echo(text)
    expected = (v.duration_sec + tr.segment_seconds - 1) // tr.segment_seconds
    findings = []
    if len(text.strip()) < t.MIN_TRANSCRIPT:
        findings.append("整份稿低於最小字數")
    if len(cache) != expected:
        findings.append("完整段數與影片切段數不同")
    if any(len(value.strip()) < t.MIN_TRANSCRIPT for value in cache.values()):
        findings.append("有片段字數過短")
    if repeats:
        findings.append("長段落完全重複，需要人工核對")
    if echoes or cleaned != text:
        findings.append("仍含轉錄請求回聲")
    segments = []
    for key, value in cache.items():
        segments.append({"start_sec": key[0], "end_sec": key[1], "chars": len(value),
                         "head": value[:400], "tail": value[-400:]})
    report = {
        "verified_at_taipei": datetime.now(t.TAIPEI).isoformat(timespec="seconds"),
        "video_id": v.id, "video_title": v.title, "duration_sec": v.duration_sec,
        "ended_at": v.ended_at.isoformat(), "privacy": v.privacy,
        "channel": "classic" if t._CHANNEL["classic"] else "interactions",
        "elapsed_sec": round(elapsed, 2), "chars": len(text),
        "production_chars": len(original),
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
        "original_sha256": hashlib.sha256(original.encode()).hexdigest(),
        "normalized_similarity": round(difflib.SequenceMatcher(
            None, re.sub(r"\s+", "", original), re.sub(r"\s+", "", text)).ratio(), 4),
        "expected_segments": expected, "completed_segments": len(cache),
        "segments": segments, "model_summary": tr.summary(),
        "day_vocabulary_count": len(vocabulary),
        "vocabulary_in_fresh_text": [name for name in vocabulary if name in text],
        "repeated_long_paragraphs": repeats, "quality_findings": findings,
        "production_sheet_writes": 0, "email_sends": 0, "line_sends": 0,
        "limitations": "格式與覆蓋檢查不能證明逐字準確；須比對原音、股名及數字。"
    }
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("video_id", "duration_sec", "channel", "elapsed_sec",
          "chars", "completed_segments", "model_summary", "quality_findings")}, ensure_ascii=False))
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
