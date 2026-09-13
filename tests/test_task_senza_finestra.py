"""Task migration contract: isolated PowerShell cmdlet fakes, never real scheduler writes."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/ops/windows/task_senza_finestra.ps1"
# The script drives the Windows Task Scheduler cmdlets. The macOS runner ships PowerShell 7 as
# `powershell` too (CI 41878d8, 13/09): the executable alone does not make the host Windows.
SHELL = shutil.which("powershell") if os.name == "nt" else None
pytestmark = pytest.mark.skipif(SHELL is None, reason="requires Windows PowerShell on Windows")


def run_scenario(tmp_path, scenario):
    project = tmp_path / "project with spaces"
    windows = project / "tools/ops/windows"
    windows.mkdir(parents=True)
    (project / "data").mkdir()
    names = ["run_price_updater.bat", "run_news_feed.bat", "run_briefing_v2.bat",
             "run_db_backup.bat", "run_consigliere_scheduled.bat"]
    for name in names:
        shutil.copyfile(ROOT / name, project / name)
        shutil.copyfile(ROOT / "tools/ops/windows" / name, windows / name)
    shutil.copyfile(ROOT / "tools/ops/windows/run_hidden.vbs", windows / "run_hidden.vbs")
    (project / "data/price_updater.log").write_text("before\n", encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"script": str(SCRIPT), "project": str(project),
                                  "output": str(tmp_path / "private"), "scenario": scenario}), encoding="utf-8")
    harness = tmp_path / "fixture.ps1"
    harness.write_text(r'''
$ErrorActionPreference = 'Stop'
$c = Get-Content -LiteralPath $args[0] -Raw | ConvertFrom-Json
. $c.script
$script:fakeNow = [datetime]'2026-09-12T12:08:00'
function Get-BBNow { return $script:fakeNow }
function Test-BBAdministrator { return $script:c.scenario -ne 'not-admin' }
$script:c = $c
$script:xmls = @{}
$script:sets = @()
$script:exports = 0
$script:states = @{}
$script:last = @{}
$names = [ordered]@{
 'Bellomberg-PriceUpdater'='run_price_updater.bat'; 'Bellomberg-NewsFeed'='run_news_feed.bat';
 'Bellomberg-Briefing'='run_briefing_v2.bat'; 'Bellomberg-AutoBackup'='run_db_backup.bat';
 'Bellomberg-Consigliere'='run_consigliere_scheduled.bat'
}
foreach ($n in $names.Keys) {
 $enabled = if ($n -eq 'Bellomberg-Consigliere') {'false'} else {'true'}
 $script:states[$n] = if ($enabled -eq 'false') {'Disabled'} else {'Ready'}
 $script:last[$n] = $script:fakeNow.AddHours(-1)
 $rootXml = [Security.SecurityElement]::Escape($c.project)
 $arg = [Security.SecurityElement]::Escape('/c "' + (Join-Path $c.project $names[$n]) + '"')
 $script:xmls[$n] = @"
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
 <RegistrationInfo><Description>synthetic fixture</Description></RegistrationInfo>
 <Triggers><CalendarTrigger><StartBoundary>2026-09-12T12:18:00</StartBoundary><Enabled>true</Enabled><ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger></Triggers>
 <Principals><Principal id="Author"><UserId>SYNTHETIC\operator</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
 <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><Enabled>$enabled</Enabled><ExecutionTimeLimit>PT5M</ExecutionTimeLimit></Settings>
 <Actions Context="Author"><Exec id="original"><Command>cmd.exe</Command><Arguments>$arg</Arguments><WorkingDirectory>$rootXml</WorkingDirectory></Exec></Actions>
</Task>
"@
}
function Get-ScheduledTask { param($TaskName,$TaskPath)
 if (!$script:xmls.ContainsKey($TaskName) -or $TaskPath -ne '\') { throw 'unexpected fake task' }
 [pscustomobject]@{TaskName=$TaskName;TaskPath=$TaskPath;State=$script:states[$TaskName]}
}
function Export-ScheduledTask { param($TaskName,$TaskPath)
 $script:exports++; return $script:xmls[$TaskName]
}
function Get-ScheduledTaskInfo { param($TaskName,$TaskPath)
 $next = $script:fakeNow.AddMinutes(10)
 if ($script:c.scenario -eq 'near-trigger') { $next = $script:fakeNow.AddSeconds(30) }
 if ($script:c.scenario -eq 'unknown-trigger') { $next = [datetime]'1899-12-30' }
 [pscustomobject]@{LastRunTime=$script:last[$TaskName];LastTaskResult=0;NextRunTime=$next;NumberOfMissedRuns=0}
}
function New-ScheduledTaskAction { param($Execute,$Argument,$WorkingDirectory,$Id)
 [pscustomobject]@{Execute=$Execute;Arguments=$Argument;WorkingDirectory=$WorkingDirectory;Id=$Id}
}
function Set-ScheduledTask { param($TaskName,$TaskPath,$Action)
 $script:sets += [pscustomobject]@{TaskName=$TaskName;Execute=$Action.Execute;Arguments=$Action.Arguments;WorkingDirectory=$Action.WorkingDirectory}
 if ($script:c.scenario -eq 'failure-before-second' -and $script:sets.Count -eq 2) { throw 'synthetic failure before mutation' }
 [xml]$x = $script:xmls[$TaskName]
 $exec = $x.SelectSingleNode('//*[local-name()="Actions"]/*[local-name()="Exec"]')
 $exec.Command = $Action.Execute; $exec.Arguments = $Action.Arguments; $exec.WorkingDirectory = $Action.WorkingDirectory
 if ($Action.Id) { $exec.SetAttribute('id',$Action.Id) }
 $script:xmls[$TaskName] = $x.OuterXml
 if ($script:c.scenario -eq 'failure-after-second' -and $script:sets.Count -eq 2) { throw 'synthetic failure after mutation' }
 if ($script:c.scenario -eq 'late-batch-drift' -and $script:sets.Count -eq 5) {
  $script:xmls['Bellomberg-PriceUpdater']=$script:xmls['Bellomberg-PriceUpdater'].Replace('PT5M','PT8M')
 }
}
function Start-ScheduledTask { throw 'FORBIDDEN Start-ScheduledTask' }
function Register-ScheduledTask { throw 'FORBIDDEN Register-ScheduledTask' }
function Enable-ScheduledTask { throw 'FORBIDDEN Enable-ScheduledTask' }
function Disable-ScheduledTask { throw 'FORBIDDEN Disable-ScheduledTask' }
$original = @{}; foreach ($n in $names.Keys) {$original[$n] = Get-BBXmlHash $script:xmls[$n]}
$failed=$false; $reason=$null; $result=$null; $plan=$null
try {
 if ($c.scenario -eq 'running') {$script:states['Bellomberg-PriceUpdater']='Running'}
 if ($c.scenario -eq 'consigliere-enabled') {
  $script:states['Bellomberg-Consigliere']='Ready'
  $script:xmls['Bellomberg-Consigliere']=$script:xmls['Bellomberg-Consigliere'].Replace('<Enabled>false</Enabled>','<Enabled>true</Enabled>')
 }
 $plan = Invoke-BBTaskTransition -ProjectRoot $c.project -OutputDirectory $c.output
 if ($c.scenario -ne 'plan') {
  if ($c.scenario -in 'trigger-drift','principal-drift','settings-drift','unknown-action') {
   $n='Bellomberg-NewsFeed'; $x=$script:xmls[$n]
   if ($c.scenario -eq 'trigger-drift') {$x=$x.Replace('12:18:00','12:19:00')}
   if ($c.scenario -eq 'principal-drift') {$x=$x.Replace('LeastPrivilege','HighestAvailable')}
   if ($c.scenario -eq 'settings-drift') {$x=$x.Replace('PT5M','PT6M')}
   if ($c.scenario -eq 'unknown-action') {$x=$x.Replace('/c &quot;','/c echo injected &amp; &quot;')}
   $script:xmls[$n]=$x
  }
  if ($c.scenario -eq 'source-drift') {Add-Content -LiteralPath (Join-Path $c.project 'run_price_updater.bat') -Value 'rem changed'}
  if ($c.scenario -eq 'expired-plan') {$script:fakeNow=$script:fakeNow.AddHours(1)}
  $result=Invoke-BBTaskTransition -ProjectRoot $c.project -Apply -PlanPath $plan.PlanPath
  if ($c.scenario -eq 'rollback-late') {$script:fakeNow=$script:fakeNow.AddHours(1)}
  if ($c.scenario -in 'rollback','rollback-late') {$result=Invoke-BBTaskTransition -ProjectRoot $c.project -Rollback -ReceiptPath $result.ReceiptPath}
  if ($c.scenario -eq 'rollback-drift') {
   $script:xmls['Bellomberg-PriceUpdater']=$script:xmls['Bellomberg-PriceUpdater'].Replace('PT5M','PT8M')
   $result=Invoke-BBTaskTransition -ProjectRoot $c.project -Rollback -ReceiptPath $result.ReceiptPath
  }
  if ($c.scenario -in 'observe','observe-late') {
   if ($c.scenario -eq 'observe-late') {$script:fakeNow=$script:fakeNow.AddHours(1)}
   $script:last['Bellomberg-PriceUpdater']=$script:fakeNow.AddMinutes(1)
   Add-Content -LiteralPath (Join-Path $c.project 'data/price_updater.log') -Value 'automatic run'
   $result=Invoke-BBTaskTransition -ProjectRoot $c.project -Observe -ReceiptPath $result.ReceiptPath
  }
 }
} catch {$failed=$true;$reason=$_.Exception.Message}
$unchanged=@(); foreach($n in $names.Keys){if ((Get-BBXmlHash $script:xmls[$n]) -eq $original[$n]) {$unchanged+=$n}}
$report=[ordered]@{failed=$failed;reason=$reason;sets=$script:sets;exports=$script:exports;unchanged=$unchanged;result=$result;plan=$plan;xmls=$script:xmls}
[Console]::WriteLine('BB_SCHEDULER_TEST '+($report|ConvertTo-Json -Depth 30 -Compress))
if($failed){exit 1}else{exit 0}
''', encoding="utf-8-sig")
    completed = subprocess.run([SHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(harness), str(config)],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=40)
    lines = [line for line in completed.stdout.splitlines() if line.startswith("BB_SCHEDULER_TEST ")]
    assert lines, completed.stdout + completed.stderr
    return completed.returncode, json.loads(lines[-1].removeprefix("BB_SCHEDULER_TEST "))


def test_plan_reads_all_xml_and_performs_zero_task_mutations(tmp_path):
    code, r = run_scenario(tmp_path, "plan")
    assert code == 0, r
    assert r["sets"] == []
    assert len(r["unchanged"]) == 5
    assert r["exports"] >= 10
    plan = Path(r["plan"]["PlanPath"])
    assert plan.is_file()
    data = json.loads(plan.read_text(encoding="utf-8-sig"))
    assert data["MutationCalls"] == 0
    assert len(data["Tasks"]) == 5
    assert all(Path(t["XmlPath"]).is_file() for t in data["Tasks"])


@pytest.mark.parametrize("scenario,reason", [
    ("trigger-drift", "TASK_DRIFT"), ("principal-drift", "TASK_DRIFT"),
    ("settings-drift", "TASK_DRIFT"), ("unknown-action", "TASK_DRIFT"),
    ("source-drift", "SOURCE_DRIFT"), ("expired-plan", "PLAN_EXPIRED"),
    ("not-admin", "ADMIN_REQUIRED"), ("running", "PLAN_UNSAFE"),
    ("near-trigger", "PLAN_UNSAFE"), ("unknown-trigger", "PLAN_UNSAFE"),
    ("consigliere-enabled", "PLAN_UNSAFE"),
])
def test_unsafe_or_changed_plan_refuses_before_first_mutation(tmp_path, scenario, reason):
    code, r = run_scenario(tmp_path, scenario)
    assert code == 1, r
    assert r["failed"] is True
    assert reason in r["reason"], r
    assert r["sets"] == [], r


def test_apply_changes_only_actions_and_keeps_root_wrappers(tmp_path):
    code, r = run_scenario(tmp_path, "apply")
    assert code == 0, r
    assert len(r["sets"]) == 5
    for action in r["sets"]:
        assert action["Execute"].lower().endswith("\\system32\\wscript.exe")
        assert action["Arguments"].startswith('//B //Nologo "')
        assert 'tools\\ops\\windows\\run_hidden.vbs' in action["Arguments"]
        assert 'tools\\ops\\windows\\run_price_updater.bat' not in action["Arguments"]
    assert '<Enabled>false</Enabled>' in r["xmls"]["Bellomberg-Consigliere"]
    receipt = json.loads(Path(r["result"]["ReceiptPath"]).read_text(encoding="utf-8-sig"))
    assert receipt["Status"] == "Applied"
    assert len(receipt["ChangedTasks"]) == 5


@pytest.mark.parametrize("scenario", ["failure-before-second", "failure-after-second"])
def test_partial_failure_restores_only_the_changed_actions(tmp_path, scenario):
    code, r = run_scenario(tmp_path, scenario)
    assert code == 1, r
    assert len(r["unchanged"]) == 5, r
    assert len(r["sets"]) in [3, 4]
    assert all(a["TaskName"] in ["Bellomberg-PriceUpdater", "Bellomberg-NewsFeed"] for a in r["sets"])


@pytest.mark.parametrize("scenario", ["rollback", "rollback-late"])
def test_explicit_rollback_restores_original_actions(tmp_path, scenario):
    code, r = run_scenario(tmp_path, scenario)
    assert code == 0, r
    assert len(r["sets"]) == 10
    assert len(r["unchanged"]) == 5


def test_rollback_refuses_any_definition_drift_without_more_mutations(tmp_path):
    code, r = run_scenario(tmp_path, "rollback-drift")
    assert code == 1, r
    assert len(r["sets"]) == 5


@pytest.mark.parametrize("scenario", ["observe", "observe-late"])
def test_observation_measures_automatic_run_and_log_growth_without_starting_tasks(tmp_path, scenario):
    code, r = run_scenario(tmp_path, scenario)
    assert code == 0, r
    assert len(r["sets"]) == 5  # Only the preceding apply.
    observations = r["result"]["Observations"]
    price = next(item for item in observations if item["TaskName"] == "Bellomberg-PriceUpdater")
    assert price["NewRun"] is True
    assert price["LogDeltaBytes"] > 0
    assert price["WindowObservation"] == "PM_REQUIRED"


def test_late_batch_drift_is_detected_and_other_actions_are_restored(tmp_path):
    code, r = run_scenario(tmp_path, "late-batch-drift")
    assert code == 1, r
    assert "APPLY_FAILED" in r["reason"]
    assert "RollbackIncomplete" in r["reason"]
    assert len(r["unchanged"]) == 4
    assert "PT8M" in r["xmls"]["Bellomberg-PriceUpdater"]
    assert len(r["sets"]) == 9
