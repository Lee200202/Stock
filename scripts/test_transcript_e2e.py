#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
逐字稿取稿的端對端演練。**只能對測試試算表跑。**

要演練的是排程真實會走的那條路，而不是「手動按一次看看」：

  第 1 幕　時間還沒到　　　→ 應該直接返回，什麼都不做
  第 2 幕　時間到了但沒影片→ 應該回報「還沒出現」，不是失敗
  第 3 幕　影片在但回放沒好→ 應該回報「直播中／回放處理中」，不是失敗
  第 4 幕　每 3 分鐘敲門　→ 真的跑輪詢迴圈，看它按規律重試
  第 5 幕　抓到最近一日　　→ 真的呼叫 Gemini，寫進測試試算表
  第 6 幕　手動優先　　　　→ 標成手動後再跑一次，必須完全停手

第 1 到 4 幕不呼叫 Gemini、不花額度。第 5 幕會真的花額度。

安全機制（三道，缺一不跑）：
  1. 必須設 TEST_SPREADSHEET_ID，程式只會寫這一張
  2. 必須設 PROD_SPREADSHEET_ID，且兩者不同
  3. 測試表的名稱必須含「測試」或 "test"

用法
  python scripts/test_transcript_e2e.py            跑第 1～4 幕（不花額度）
  python scripts/test_transcript_e2e.py --live     連第 5、6 幕一起跑（會花額度）
  python scripts/test_transcript_e2e.py --date 2026/09/18 --live
"""

import argparse
import os
import sys
import time
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from market_holidays import is_trading_day   # noqa: E402


def die(msg):
    print(f"\n\033[1;31m✗ {msg}\033[0m\n", file=sys.stderr)
    sys.exit(1)


def guard():
    """三道防線。任何一道不過就不跑——寧可不測，也不要污染正式資料。"""
    test_id = os.environ.get("TEST_SPREADSHEET_ID", "").strip()
    prod_id = os.environ.get("PROD_SPREADSHEET_ID", "").strip()

    if not test_id:
        die("沒有設 TEST_SPREADSHEET_ID。\n"
            "  請先另外開一張試算表（名稱要含「測試」），分享給服務帳號，\n"
            "  再把它的 ID 設進 TEST_SPREADSHEET_ID。")
    if not prod_id:
        die("沒有設 PROD_SPREADSHEET_ID。\n"
            "  這一項是用來比對的：不知道正式表是哪一張，就沒辦法確認沒寫錯地方。")
    if test_id == prod_id:
        die("TEST_SPREADSHEET_ID 與 PROD_SPREADSHEET_ID 相同。\n"
            "  這代表你正指著正式試算表，演練會污染真實資料。停止。")

    # 真正打開來看名稱。ID 貼錯成另一張正式表時，前兩道都攔不住。
    os.environ["SPREADSHEET_ID"] = test_id
    import transcript as T
    ss = T.open_sheets()
    title = getattr(ss, "title", "") or ""
    if not any(k in title.lower() for k in ("測試", "test")):
        die(f"打開的試算表叫「{title}」，名稱裡沒有「測試」或 test。\n"
            f"  為了避免 ID 貼錯，這裡要求測試表的名稱看得出是測試用的。")
    print(f"  安全檢查通過：寫入目標是「{title}」")
    return T, ss


def banner(n, title):
    print(f"\n\033[1;36m{'─' * 62}\n第 {n} 幕　{title}\n{'─' * 62}\033[0m")


def expect(got, want, what):
    ok = got == want
    mark = "\033[1;32m通過\033[0m" if ok else "\033[1;31m不符\033[0m"
    print(f"  [{mark}] {what}：得到 {got}，預期 {want}")
    return ok


def main():
    ap = argparse.ArgumentParser(description="逐字稿取稿端對端演練（只對測試表）")
    ap.add_argument("--date", help="第 5 幕要抓哪一天，預設抓頻道最近一集")
    ap.add_argument("--live", action="store_true",
                    help="連第 5、6 幕一起跑。會真的呼叫 Gemini、花額度")
    ap.add_argument("--rounds", type=int, default=2,
                    help="第 4 幕要看幾輪輪詢（預設 2 輪，約 3 分鐘）")
    args = ap.parse_args()

    print("\033[1m逐字稿取稿端對端演練\033[0m")
    T, ss = guard()

    results = []

    # ---- 第 1 幕：時間還沒到 ---- #
    banner(1, "時間還沒到，排程應該直接返回")
    T.POLL_START, T.POLL_UNTIL = "23:58", "23:59"
    # 要挑一個「真的會開盤」的日子，否則會走到休市那個分支，
    # 退出碼一樣是 0，但驗到的就不是「時間還沒到」這件事了。
    probe = date.today()
    for _ in range(14):
        if is_trading_day(probe):
            break
        probe -= timedelta(days=1)
    print(f"  用 {probe:%Y/%m/%d}（交易日）測「時間還沒到」")
    code = T.cmd_auto(argparse.Namespace(date=probe, once=True, force=False))
    results.append(expect(code, 0, "退出碼（沒到時間要綠燈結束）"))

    # ---- 第 2 幕：時間到了，但那一天沒有影片 ---- #
    banner(2, "時段內，但目標日沒有影片")
    T.POLL_START, T.POLL_UNTIL = "00:00", "23:59"
    # 挑一個一定沒有影片的未來平日
    future = date.today() + timedelta(days=30)
    while not is_trading_day(future):
        future += timedelta(days=1)
    code = T.cmd_auto(argparse.Namespace(date=future, once=True, force=False))
    results.append(expect(code, 0, "退出碼（沒影片是常態，不該紅燈）"))

    # ---- 第 3 幕：回放就緒判斷 ---- #
    banner(3, "回放就緒判斷（不呼叫 Gemini）")
    target = T.parse_date(args.date) if args.date else None
    if target is None:
        feed = [v for v in T.fetch_feed() if T.is_target(v.title)]
        if not feed:
            die("頻道上找不到任何符合關鍵字的影片，無法繼續。")
        target = feed[0].date
        print(f"  未指定日期，取頻道最近一集：{target:%Y/%m/%d}")
    v = T.find_video(target)
    if not v:
        die(f"{target:%Y/%m/%d} 找不到影片。用 --date 指定一個有影片的日子。")
    now = datetime.now(T.TAIPEI)
    print(f"  影片　{v.title}")
    print(f"  片長　{T.hms(v.duration_sec)}　狀態　{v.status_text(now)}")
    print(f"  可轉錄？{v.is_ready(now)}")
    results.append(expect(v.detailed, True,
                          "有沒有拿到片長與結束時間（沒有代表缺 YOUTUBE_API_KEY）"))

    # ---- 第 4 幕：真的跑輪詢迴圈 ---- #
    banner(4, f"每 {T.POLL_INTERVAL} 秒敲一次門，看 {args.rounds} 輪")
    print("  這一幕故意指向沒有影片的未來日期，所以每一輪都會回報「還沒出現」，")
    print("  重點是看它有沒有按規律重試、有沒有在時間預算內收工。")
    saved_budget = T.TIME_BUDGET
    T.TIME_BUDGET = T.POLL_INTERVAL * args.rounds + 5
    t0 = time.monotonic()
    code = T.cmd_auto(argparse.Namespace(date=future, once=False, force=False))
    spent = time.monotonic() - t0
    budget_used = T.TIME_BUDGET
    T.TIME_BUDGET = saved_budget
    results.append(expect(code, 0, "退出碼"))
    print(f"  實際跑了 {spent / 60:.1f} 分鐘（本幕預算 {budget_used / 60:.1f} 分鐘）")

    if not args.live:
        print("\n\033[1;33m第 5、6 幕需要 --live（會真的呼叫 Gemini、花額度），已跳過。\033[0m")
    else:
        # ---- 第 5 幕：真的抓一集 ---- #
        banner(5, f"真的抓 {target:%Y/%m/%d} 這一集（會花額度）")
        code = T.cmd_auto(argparse.Namespace(date=target, once=True, force=True))
        results.append(expect(code, 0, "退出碼"))
        ws, header, idx, row = T.read_state(ss, "", target.strftime("%Y/%m/%d"))
        raw = str(row.get(T.COL_RAW) or "")
        print(f"  試算表第 {idx} 列　{len(raw)} 字　來源「{T.source_of(row)}」")
        results.append(expect(len(raw) > T.MIN_TRANSCRIPT, True, "逐字稿長度足夠"))
        results.append(expect(T.source_of(row), T.SRC_AUTO, "來源標記"))

        # ---- 第 6 幕：手動優先 ---- #
        banner(6, "標成手動之後，自動化必須完全停手")
        a1 = T.col_a1(header, T.COL_SOURCE, idx)
        T.sheets_retry(ws.update, range_name=a1, values=[[T.SRC_MANUAL]],
                       value_input_option="RAW")
        called = []
        real_find = T.find_video
        T.find_video = lambda d: called.append(d) or real_find(d)
        try:
            code = T.cmd_auto(argparse.Namespace(date=target, once=True, force=True))
        finally:
            T.find_video = real_find
        results.append(expect(code, 0, "退出碼"))
        results.append(expect(called, [], "有沒有去查 YouTube（手動優先應該連查都不查）"))

    # ---- 總結 ---- #
    print(f"\n\033[1m{'═' * 62}\033[0m")
    bad = results.count(False)
    if bad:
        print(f"\033[1;31m{bad} 項不符預期，請看上面標示。\033[0m")
    else:
        print(f"\033[1;32m全部 {len(results)} 項符合預期。\033[0m")
    print("\n接下來把逐字稿交給潤飾稽核流程（同樣指向測試表）：")
    print("  SPREADSHEET_ID=$TEST_SPREADSHEET_ID python pipeline.py")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
