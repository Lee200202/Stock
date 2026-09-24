// 本機介面驗收用假資料；不連正式試算表、不發信、不保存管理密鑰。
const fs = require('fs');
const path = require('path');
const root = path.resolve(__dirname, '..');
const read = name => fs.readFileSync(path.join(root, 'apps-script', name), 'utf8');
const out = path.join(root, 'docs', '0924-v57', 'preview-admin.html');
const mock = {
  ok: true, today: '2026/09/24', now: '14:12', timeline: [
    { time: '11:15', kind: '盤中即時信', text: '郵件服務接受 2/2 位', where: '寄送帳本' },
    { time: '12:05', kind: '每日整理信', text: '郵件服務接受 2/2 位', where: '寄送帳本' }
  ], ops: {
    trading: true, noShow: false, plannedNoShow: false, videoStatus: '完成', rawChars: 23493, polishedChars: 16359,
    mailStatus: '已寄送', mailStart: '12:00', smsCount: 1,
    deliveries: { total: 4, accepted: 4, open: 0, failed: 0, unknown: 0 }, mailQuotaLeft: 96,
    mailByKind: {
      daily: { messages: 1, total: 2, accepted: 2, open: 0, failed: 0, unknown: 0, firstAt: '2026/09/24 12:05:00' },
      sms: { messages: 1, total: 2, accepted: 2, open: 0, failed: 0, unknown: 0, firstAt: '2026/09/24 11:15:00', lastAt: '2026/09/24 11:15:01' }
    },
    heartbeatAge: 2, heartbeatAt: '14:10', missingTriggers: [], triggerError: '', alerts: [],
    blankExits: ['力旺 2026/09/23'], dailyK: { finishedAt: '2026/09/23 16:54', lastCode: '9999' },
    marketScheduled: true, marketSample: { at: '2026/09/24 14:10:00', ok: true, note: '' },
    hourlyScheduled: false, hourlyProgress: '3529｜24/33｜2026/09/23', perfLast: '2026/09/23'
  }
};
let html = read('Admin.html')
  .replace("<?!= include('Stylesheet'); ?>", read('Stylesheet.html'))
  .replace("<?!= include('Settings'); ?>", read('Settings.html'))
  .replace("<?!= include('Changelog'); ?>", read('Changelog.html'))
  .replaceAll('<?= webAppUrl ?>', 'https://example.invalid/exec');
const stub = `<script>window.google={script:{run:{withSuccessHandler:function(fn){this.ok=fn;return this;},withFailureHandler:function(){return this;},apiAdminTodayStatus:function(){this.ok(${JSON.stringify(mock)});return this;}}}};</script>`;
html = html.replace('<script>\nfunction $(id)', stub + '\n<script>\nfunction $(id)');
html = html.replace('</body>', `<script>KEY='preview';document.documentElement.dataset.theme='dark';document.getElementById('loginWrap').style.display='none';document.getElementById('app').style.display='block';document.querySelector('[data-tab="ops"]').click();</script></body>`);
fs.mkdirSync(path.dirname(out), { recursive: true });
fs.writeFileSync(out, html);
console.log(out);
