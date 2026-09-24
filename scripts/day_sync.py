"""逐日編輯的 Actions 協調器；游標在 GAS，重送不重做已完成步驟，不寄信。"""
import os
import sys
import time
import requests


def call(session, url, key, **params):
    for attempt in range(3):
        try:
            response = session.get(url, params=dict(action='day-sync', key=key, **params), timeout=(20, 360))
            response.raise_for_status()
            data = response.json()
            if not data.get('ok'):
                raise RuntimeError(data.get('error', '同步未完成'))
            return data
        except (requests.RequestException, ValueError):
            # 不輸出 exception：requests 的訊息可能包含 URL 裡的管理密鑰。
            if attempt == 2:
                raise RuntimeError('GAS 暫時未回有效 JSON；游標保留，由後端 watchdog 接續') from None
            time.sleep(8 * (attempt + 1))


def main():
    url, key = os.environ['APPS_SCRIPT_URL'], os.environ['ADMIN_KEY']
    session = requests.Session()
    deadline = time.monotonic() + 23 * 60
    mode = os.environ.get('MODE') or 'queue'
    if mode not in ('queue', 'performance', 'cancel', 'cancel-performance'):
        raise RuntimeError('不支援的同步模式：' + mode)
    data = call(session, url, key, op='state')
    state = data.get('state') or {}
    active = state.get('status') in ('處理中', '等待續跑')
    if mode in ('cancel', 'cancel-performance'):
        if active:
            job_id = state.get('id')
            if not job_id:
                raise RuntimeError('後端沒有回傳工單 ID，未執行取消')
            data = call(session, url, key, op='cancel', id=job_id)
            state = data.get('state') or {}
            if state.get('id') != job_id or state.get('status') not in ('已取消', '完成'):
                raise RuntimeError('無法確認原工單已取消，停止後續重建')
            print(f'{state["status"]} {job_id}：{data.get("message", "")}')
            if mode == 'cancel-performance':
                while (data.get('health') or {}).get('running'):
                    if time.monotonic() >= deadline:
                        raise RuntimeError('已取消工單，但原步驟仍在執行；請稍後重跑 performance')
                    time.sleep(15)
                    data = call(session, url, key, op='state')
                    state = data.get('state') or {}
                    if state.get('id') != job_id:
                        raise RuntimeError('等待收尾期間出現新工單，未開始績效重建')
        else:
            print('目前沒有進行中或等待續跑的工單。')
        if mode == 'cancel':
            return
        data = call(session, url, key, op='performance')
    elif mode == 'performance':
        if active and str(state.get('id', '')).startswith('PERF-'):
            print('接續現有績效重建工單。')
        elif active:
            raise RuntimeError('已有逐日更新工單 ' + str(state.get('id', '')) +
                               '；請使用 cancel-performance 明確取消後重建，或先以 queue 完成該工單')
        else:
            data = call(session, url, key, op='performance')
    while time.monotonic() < deadline:
        state = data.get('state') or {}
        if not state or state.get('status') in ('完成', '已取消'):
            print(state.get('status', '沒有待處理工單')); return
        print(f"{state.get('date')} {state.get('index')}/{state.get('total')} {state.get('step')}：{state.get('status')}", flush=True)
        if state.get('status') == '等待續跑':
            print('後端已保留檢查點，等待下一次自動續跑。'); return
        data = call(session, url, key, op='step', id=state['id'], index=state['index'])
        time.sleep(3)
    print('本棒時間到，後端檢查點與 watchdog 將接續。')


if __name__ == '__main__':
    try:
        main()
    except RuntimeError as exc:
        print(f'::error::{exc}', file=sys.stderr)
        sys.exit(1)
