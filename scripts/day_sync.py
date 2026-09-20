"""逐日編輯的 Actions 協調器；游標在 GAS，重送不重做已完成步驟，不寄信。"""
import os
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
    data = call(session, url, key, op='performance' if os.environ.get('MODE') == 'performance' else 'state')
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
    main()
