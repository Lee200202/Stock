/**
 * 測試副本專用防護。由 New-TestWorkspace.ps1 接到既有入口。
 * 不應直接合併到正式版。設定缺失時停止，不退回正式環境。
 */
function assertTestEnvironment_() {
  var p = PropertiesService.getScriptProperties();
  var expected = p.getProperty('TEST_SPREADSHEET_ID');
  if (p.getProperty('APP_ENV') !== 'test' || !expected ||
      expected !== p.getProperty('SPREADSHEET_ID') ||
      expected === p.getProperty('PRODUCTION_SPREADSHEET_ID') ||
      !p.getProperty('PRODUCTION_SPREADSHEET_ID') ||
      ScriptApp.getScriptId() !== p.getProperty('TEST_SCRIPT_ID')) {
    throw new Error('TEST_GUARD: 請核對環境、測試 Script ID、測試與正式 Sheet ID。');
  }
  return p;
}

/** 驗證後台派工目標，避免原程式未設定 ref 時回退 main。 */
function assertTestGithub_() {
  var p = assertTestEnvironment_();
  if (p.getProperty('GITHUB_REPO') !== 'Lee200202/Stock' ||
      p.getProperty('GITHUB_REF') !== 'codex/test' ||
      p.getProperty('GITHUB_WORKFLOW') !== 'daily.yml') {
    throw new Error('TEST_GUARD: 派工只允許 Lee200202/Stock / codex/test / daily.yml。');
  }
}

/**
 * 攔截目前 11 個 MailApp.sendEmail 呼叫點。
 * 預設拋錯，不假裝寄送成功。若啟用，只送到 TEST_EMAIL 單一信箱。
 * 不沿用 cc、bcc、replyTo，避免把測試內容送給原收件者。
 * @param {Object} message 既有程式的郵件物件。
 */
function testSendEmail_(message) {
  var p = assertTestEnvironment_();
  if (p.getProperty('TEST_MAIL_ENABLED') !== 'true') {
    throw new Error('TEST_MAIL_BLOCKED: 測試環境預設不寄信。');
  }
  var recipient = String(p.getProperty('TEST_EMAIL') || '').trim();
  if (!/^[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+$/.test(recipient)) {
    throw new Error('TEST_MAIL_BLOCKED: TEST_EMAIL 必須是單一有效信箱。');
  }
  if (!message || typeof message !== 'object') {
    throw new Error('TEST_MAIL_BLOCKED: 不支援的寄信呼叫格式。');
  }
  var safe = {to: recipient, subject: '[TEST] ' + String(message.subject || ''),
    body: message.body || '測試郵件，請以 HTML 模式閱讀。'};
  ['htmlBody', 'attachments', 'inlineImages', 'name'].forEach(function (key) {
    if (message[key] !== undefined) safe[key] = message[key];
  });
  MailApp.sendEmail(safe);
}

/** 編輯器手動執行；不建立觸發器、不寄信、不輸出密鑰。 */
function checkTestEnvironment() {
  assertTestGithub_();
  var ss = getSS_();
  Logger.log(JSON.stringify({environment: 'test', scriptId: ScriptApp.getScriptId(),
    spreadsheetId: ss.getId(), spreadsheetName: ss.getName(),
    installedTriggers: ScriptApp.getProjectTriggers().length,
    mailEnabled: PROP.getProperty('TEST_MAIL_ENABLED') === 'true'}));
}
