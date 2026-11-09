# 워커를 창 없이 띄운다(SPEC v1.7: 이 PC + 로그온 때 자동 시작). 이미 떠 있으면 아무것도 하지 않는다.
# 작업 스케줄러 등록(한 번, 관리자 권한 필요 없음):
#   $a = New-ScheduledTaskAction -Execute "pwsh.exe" -Argument '-NoProfile -WindowStyle Hidden -File "C:\TechRadarAgent\scripts\start-worker.ps1"'
#   Register-ScheduledTask -TaskName "TechRadarWorker" -Action $a -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME) `
#     -Settings (New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit 0)
# 해제: Unregister-ScheduledTask -TaskName "TechRadarWorker" -Confirm:$false
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$logs = Join-Path $root "logs"
$pidFile = Join-Path $logs "worker.pid"
New-Item -ItemType Directory -Force $logs | Out-Null

if (Test-Path $pidFile) {
    # pid가 다른 프로세스에 다시 쓰였을 수 있어 명령줄까지 본다. 워커가 둘이면 같은 잡을 두 번 집지는 않지만 호출 간격이 깨진다
    $old = Get-CimInstance Win32_Process -Filter ("ProcessId = " + [int](Get-Content $pidFile)) -ErrorAction SilentlyContinue
    if ($old -and $old.CommandLine -like "*-m radar worker*") { exit 0 }
}

$log = Join-Path $logs "worker.log"
if (Test-Path $log) { Move-Item $log (Join-Path $logs ("worker-" + (Get-Date -Format "yyyyMMdd-HHmm") + ".log")) -Force }
$worker = Start-Process -FilePath (Join-Path $root ".venv\Scripts\python.exe") -ArgumentList "-X", "utf8", "-m", "radar", "worker" `
    -WorkingDirectory $root -WindowStyle Hidden -PassThru `
    -RedirectStandardError $log -RedirectStandardOutput (Join-Path $logs "worker.out.log")
Set-Content $pidFile $worker.Id
