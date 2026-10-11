"""Historical, read-only regression from the official 2026/09/29–10/02 mail view.

Only public article excerpts are kept here. No model request or production write occurs.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("SPREADSHEET_ID", "test")
os.environ.setdefault("GEMINI_API_KEY", "AIzaSyDUMMY_local_import_only_0000000000")
os.environ.setdefault("GOOGLE_SERVICE_ACCOUNT_JSON", "{}")
sys.path.insert(0, str(ROOT / "pipeline"))
import pipeline as pl  # noqa: E402


def point(text, kind="view"):
    return {"text": text, "kind": kind, "_evidence_verified": True}


def check_repeated_points():
    # 9/29: two teaching bullets repeat the same two seasonal-line entry points.
    sep29 = {"market": [
        point("股票正確的買點分為兩種：季線以下先布局，或突破後等拉回測季線再買；不在季線向下時追高。"),
        point("買股的正確節奏與買點：第一種在季線以下布局，第二種突破後拉回測季線再買。不要追逐突破季線的高點。"),
    ]}
    # 9/30: teaching repeats the market bullet's long-term MACD claim verbatim.
    sep30 = {"market": [
        point("大盤方向沒有變過，加權指數月K線MACD自去年四月翻紅後未轉弱，長線趨勢仍向上。", "macro"),
        point("大盤方向沒有變過，加權指數月K線MACD自去年四月翻紅後未轉弱，長線趨勢仍向上。"),
    ]}
    # 10/01: one bullet is intraday index action; another is a news-based entry rule.
    oct01 = {"market": [
        point("台股早盤拉高後回落，十點多止跌回穩；觀察短線賣壓是否消化，再評估大盤走勢。", "macro"),
        point("利多新聞若在股價大漲後才公布，追價風險較高；低檔盤整時的消息要結合位置與拉回買點評估。"),
    ]}
    # 10/02: first two market bullets both report expanded volume above monthly mean.
    oct02 = {"market": [
        point("台股成交量放大到九千多億元，突破月均量八千六百多億元；市場在低檔換手，盤面結構仍穩健。", "macro"),
        point("台股成交量放大到九千多億元，突破月均量八千六百多億元；市場在低檔換手，盤面結構仍穩健。", "macro"),
    ]}
    assert pl.dedupe_market_points(sep29) == 1, "9/29 教學重點仍重複"
    assert pl.dedupe_market_points(sep30) == 1, "9/30 跨章重點仍重複"
    assert pl.dedupe_market_points(oct01) == 0, "10/01 不同主題被誤刪"
    assert pl.dedupe_market_points(oct02) == 1, "10/02 成交量重點仍重複"
    assert pl._title_drop_repeats("今天的成交量不一樣，今天的成交量不一樣，量放大了") == "今天的成交量不一樣，量放大了"


def check_stock_classification_and_late_duplicates():
    # The 10/02 transcript explicitly says 台達電 can be bought on a decline,
    # while not chasing a rise. That is a conditional positive signal.
    delta = "台達電季線翻揚，股價下跌時可以留意買點，但上漲時不要追價。"
    assert pl.watch_tone(delta) == "watch_watch", "條件買點被誤判成不碰"
    # 9/30 勤誠：尚未出現底部訊號，明講「還不能進場買進」。
    not_yet = "勤誠受到ETF賣壓，底部背離尚未出現，還不能進場買進，需等待指標翻揚。"
    assert pl.watch_tone(not_yet) == "watch_avoid", "明講尚不能進場卻被未來條件改成注意"
    rows = {"holdings": [
        {"code": "5536", "name": "聖暉*", "note": "昨日上漲，今日拉回屬於洗盤。", "stance": "持有"},
        {"code": "5536", "name": "聖暉*", "note": "現有部位續抱。", "stance": "持有"},
    ], "buy": [
        {"code": "5536", "name": "聖暉*", "price": "900", "seq": 1},
        {"code": "5536", "name": "聖暉*", "price": "910", "seq": 2},
    ]}
    pl.merge_duplicates(rows)
    assert len(rows["holdings"]) == 1, "同日持股重複列"
    assert len(rows["buy"]) == 2, "不同買入事件不應合併"
    rows["holdings"].append({"code": "5536", "name": "聖暉*", "note": "前一天的獨立持股紀錄。", "_date": "2026/10/01"})
    rows["holdings"][0]["_date"] = "2026/10/02"
    pl.merge_duplicates(rows)
    assert len(rows["holdings"]) == 2, "跨日持股不得合併"


def check_written_stock_notes():
    # 9/29 預覽曾把講者的比喻、口頭疊字原封寫進公開說明。
    for oral in ("台積電過新高只是打一個噴嚏。", "外資賣一賣後又買回來。", "股價咻咻咻向上。"):
        assert "俚語或口頭禪" in pl.written_style_issues(oral), oral
    assert not pl.written_style_issues("台積電維持在季線之上，後續觀察能否突破前高。")


def check_late_named_refusal():
    # 9/30 先指著圖說不碰，直到下一句才報股名；昨天的跌幅是理由，禁買是今天的結論。
    sentences = ["這一支，你們還敢碰嗎？", "當然不敢碰，昨天大跌。", "缺口被補，不要碰。",
                 "所以你來看，這是甲公司，對不對？", "接下來看乙公司。"]
    assert pl._late_named_refusal(sentences, 3)
    assert not pl._late_named_refusal(sentences, 4)
    assert not pl._late_named_refusal(["這一支昨天跌很多。", "不要碰。", "這是甲公司。"], 2)


if __name__ == "__main__":
    check_repeated_points()
    check_stock_classification_and_late_duplicates()
    check_written_stock_notes()
    check_late_named_refusal()
    print("9/29–10/02 歷史回歸：重點去重、相異主題、條件買點、持股合併與分筆成交通過")
