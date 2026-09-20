#requires -Version 5.1
<#
.SYNOPSIS
Clone Stock into a new sibling folder, then overlay an isolated local test snapshot.
.DESCRIPTION
Only reads the source. Refuses an existing destination. Does not push, deploy,
create cloud resources, install triggers, send email, or copy credentials.
#>
[CmdletBinding()]
param(
    [string]$Source = 'C:\Users\user\Downloads\zhangzhen-stock-site-updated',
    [string]$Destination = 'C:\Users\user\Downloads\zhangzhen-stock-site-test'
)
$ErrorActionPreference = 'Stop'
$sourceRoot = (Resolve-Path -LiteralPath $Source).Path.TrimEnd('\', '/')
$targetRoot = [IO.Path]::GetFullPath($Destination).TrimEnd('\', '/')
if ($targetRoot -eq $sourceRoot -or $targetRoot.StartsWith($sourceRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Destination must be outside the source folder.'
}
if (Test-Path -LiteralPath $targetRoot) { throw 'Destination already exists; choose a new empty path.' }
foreach ($required in @('requirements.txt','apps-script/Setup.gs','.github/workflows/daily.yml')) {
    if (!(Test-Path -LiteralPath (Join-Path $sourceRoot $required))) { throw "Missing source file: $required" }
}
$pipelineSource = Join-Path $sourceRoot 'pipeline.py'
$nestedPipeline = Join-Path $sourceRoot 'pipeline/pipeline.py'
if (!(Test-Path -LiteralPath $pipelineSource)) { $pipelineSource = $nestedPipeline }
if (!(Test-Path -LiteralPath $pipelineSource)) { throw 'Neither pipeline.py nor pipeline/pipeline.py exists.' }
if ((Test-Path -LiteralPath $nestedPipeline) -and
    (Get-FileHash -LiteralPath $pipelineSource).Hash -ne (Get-FileHash -LiteralPath $nestedPipeline).Hash) {
    throw 'Source pipeline copies differ; resolve the source version before creating a test snapshot.'
}
Get-Command git -ErrorAction Stop | Out-Null

function Write-Utf8([string]$Path, [string]$Content) {
    [IO.File]::WriteAllText($Path, $Content, (New-Object Text.UTF8Encoding($false)))
}
function Replace-Once([string]$Text, [string]$Needle, [string]$Replacement) {
    if ([regex]::Matches($Text, [regex]::Escape($Needle)).Count -ne 1) {
        throw "Source changed; expected exactly one marker: $Needle"
    }
    return $Text.Replace($Needle, $Replacement)
}

# A fresh clone prevents Git from inheriting C:\Users\user\.git.
& git clone -- 'https://github.com/Lee200202/Stock.git' $targetRoot
if ($LASTEXITCODE -ne 0) { throw 'Clone failed. Source is unchanged; partial destination is retained for inspection.' }
& git -C $targetRoot switch -c codex/test
if ($LASTEXITCODE -ne 0) { throw 'Could not create codex/test. Inspect the new clone before proceeding.' }

# Whitelist source files; never bring local secrets, backups or auth caches across.
foreach ($folder in @('apps-script','pipeline','scripts','tests')) {
    $inputDir = Join-Path $sourceRoot $folder
    if (!(Test-Path -LiteralPath $inputDir)) { continue }
    foreach ($file in Get-ChildItem -LiteralPath $inputDir -Recurse -File) {
        if ($file.FullName -match '[\\/](__pycache__|node_modules|\.venv)[\\/]') { continue }
        if ($file.Extension -notin @('.gs','.html','.py','.js')) { continue }
        $relative = $file.FullName.Substring($sourceRoot.Length + 1)
        $output = Join-Path $targetRoot $relative
        New-Item -ItemType Directory -Path (Split-Path $output) -Force | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $output
    }
}
foreach ($file in @('requirements.txt','README.md')) {
    Copy-Item -LiteralPath (Join-Path $sourceRoot $file) -Destination (Join-Path $targetRoot $file)
}
New-Item -ItemType Directory -Path (Join-Path $targetRoot 'pipeline') -Force | Out-Null
Copy-Item -LiteralPath $pipelineSource -Destination (Join-Path $targetRoot 'pipeline.py')
Copy-Item -LiteralPath $pipelineSource -Destination (Join-Path $targetRoot 'pipeline/pipeline.py')

# Park every inherited workflow on the TEST branch; preserve main unchanged.
$workflowDir = Join-Path $targetRoot '.github/workflows'
New-Item -ItemType Directory -Path $workflowDir -Force | Out-Null
foreach ($file in Get-ChildItem -LiteralPath $workflowDir -File) {
    if ($file.Extension -in @('.yml','.yaml')) {
        Rename-Item -LiteralPath $file.FullName -NewName ($file.Name + '.disabled')
    }
}
$workflow = Get-Content -LiteralPath (Join-Path $sourceRoot '.github/workflows/daily.yml') -Raw -Encoding UTF8
$workflow = [regex]::Replace($workflow, '(?ms)^  schedule:\r?\n.*?(?=^  workflow_dispatch:)', '')
$workflow = [regex]::Replace($workflow, '\bsecrets\.(?!TEST_)([A-Z][A-Z0-9_]*)', 'secrets.TEST_$1')
$workflow = [regex]::Replace($workflow, '\bvars\.(?!TEST_)([A-Z][A-Z0-9_]*)', 'vars.TEST_$1')
$workflow = [regex]::Replace($workflow, '(?m)^  group:.*$', '  group: stock-test-${{ github.ref }}')
$workflow = Replace-Once $workflow 'name: daily-transcript-pipeline' 'name: test-transcript-pipeline'
$workflow = Replace-Once $workflow '    runs-on: ubuntu-latest' @'
    if: github.ref == 'refs/heads/codex/test' && github.event_name == 'workflow_dispatch'
    environment: test
    runs-on: ubuntu-latest
'@
$workflow = Replace-Once $workflow '    steps:' @'
    steps:
      - name: Validate isolated test configuration before any data access
        env:
          TEST_SHEET: ${{ secrets.TEST_SPREADSHEET_ID }}
          EXPECTED_SHEET: ${{ vars.TEST_EXPECTED_SPREADSHEET_ID }}
          PRODUCTION_SHEET: ${{ vars.TEST_PRODUCTION_SPREADSHEET_ID }}
          TEST_SA: ${{ secrets.TEST_GOOGLE_SHEETS_SERVICE_ACCOUNT }}
          EXPECTED_SA: ${{ vars.TEST_SERVICE_ACCOUNT_EMAIL }}
          TEST_URL: ${{ secrets.TEST_APPS_SCRIPT_URL }}
          EXPECTED_URL: ${{ vars.TEST_EXPECTED_APPS_SCRIPT_URL }}
          TEST_ADMIN: ${{ secrets.TEST_ADMIN_KEY }}
        run: |
          python - <<'PY'
          import json, os
          e = os.environ
          required = ('TEST_SHEET', 'EXPECTED_SHEET', 'PRODUCTION_SHEET', 'TEST_SA',
                      'EXPECTED_SA', 'TEST_URL', 'EXPECTED_URL', 'TEST_ADMIN')
          if not all(e.get(k, '').strip() for k in required):
              raise SystemExit('Missing isolated test configuration')
          if e['TEST_SHEET'] != e['EXPECTED_SHEET'] or e['TEST_SHEET'] == e['PRODUCTION_SHEET']:
              raise SystemExit('Test spreadsheet mismatch')
          if json.loads(e['TEST_SA']).get('client_email') != e['EXPECTED_SA']:
              raise SystemExit('Test service account mismatch')
          if e['TEST_URL'] != e['EXPECTED_URL'] or not e['TEST_URL'].endswith('/exec'):
              raise SystemExit('Test Web App URL mismatch')
          print('PASS: isolated test configuration; no credentials printed')
          PY
'@
if ($workflow -match '(?m)^  schedule:' -or $workflow -match '\bsecrets\.(?!TEST_)') {
    throw 'Workflow isolation check failed.'
}
Write-Utf8 (Join-Path $workflowDir 'daily.yml') $workflow

# Adapt only the test snapshot; fail loudly if source entry points changed.
$gasRoot = Join-Path $targetRoot 'apps-script'
$mailCalls = 0
foreach ($file in Get-ChildItem -LiteralPath $gasRoot -Filter '*.gs' -File) {
    if ($file.Name -eq 'TestEnvironment.gs') { throw 'Clone already contains TestEnvironment.gs; review before overlay.' }
    $content = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8
    $mailCalls += [regex]::Matches($content, 'MailApp\.sendEmail\(').Count
    $content = $content.Replace('MailApp.sendEmail(', 'testSendEmail_(')
    if ($file.Name -eq 'Setup.gs') {
        $content = Replace-Once $content 'function getSS_() {' "function getSS_() {`n  assertTestEnvironment_();"
        $content = Replace-Once $content 'function installTriggers() {' "function installTriggers() {`n  throw new Error('TEST: bulk trigger installation disabled; see phase1 manual.');"
    }
    if ($file.Name -eq 'Code.gs') {
        $content = Replace-Once $content 'function doGet(e) {' "function doGet(e) {`n  assertTestEnvironment_();"
        $content = $content.Replace("var APP_TITLE = '", "var APP_TITLE = '[TEST] ")
    }
    if ($file.Name -eq 'Adminservice.gs') {
        $content = Replace-Once $content 'function githubCfg_() {' "function githubCfg_() {`n  assertTestGithub_();"
    }
    Write-Utf8 $file.FullName $content
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'TestEnvironment.gs') -Destination (Join-Path $gasRoot 'TestEnvironment.gs')
Write-Utf8 (Join-Path $gasRoot 'appsscript.json') @'
{
  "timeZone": "Asia/Taipei",
  "dependencies": {},
  "exceptionLogging": "STACKDRIVER",
  "runtimeVersion": "V8"
}
'@
Write-Utf8 (Join-Path $targetRoot '.clasp.json.example') @'
{
  "scriptId": "REPLACE_WITH_NEW_TEST_SCRIPT_ID",
  "rootDir": "apps-script"
}
'@
Write-Utf8 (Join-Path $targetRoot '.claspignore') "**/**`n!*.gs`n!*.html`n!appsscript.json`n"
$ignorePath = Join-Path $targetRoot '.gitignore'
$ignore = if (Test-Path -LiteralPath $ignorePath) { Get-Content -LiteralPath $ignorePath -Raw } else { '' }
$ignore += "`n.clasp.json`n.clasprc*.json`n.env`n.env.*`n!.env.example`ncredentials/`nfixtures/private/`nartifacts/`n__pycache__/`n*.pyc`n.venv/`nnode_modules/`n*service-account*.json`nstorage_state.json`n"
Write-Utf8 $ignorePath $ignore
foreach ($folder in @('credentials','fixtures/private','artifacts','docs/phase1')) {
    New-Item -ItemType Directory -Path (Join-Path $targetRoot $folder) -Force | Out-Null
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'DEPLOY_TEST.md') -Destination (Join-Path $targetRoot 'docs/phase1/DEPLOY_TEST.md')
Write-Output "Created local test snapshot: $targetRoot"
Write-Output "Mail call sites routed through test guard: $mailCalls"
Write-Output 'Next: review git diff, create NEW cloud resources, and follow DEPLOY_TEST.md. Nothing pushed or deployed.'
