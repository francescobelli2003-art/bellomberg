<#
Plan (default) reads the five existing tasks and writes private XML/JSON evidence.
Apply changes only Actions, using the real root batch wrappers and run_hidden.vbs.
Rollback restores only recorded Actions after a complete definition drift check.
Observe reads the next automatic run; it never starts a task or certifies a window.
No installer, task registration, task enable/disable, registry or policy changes.
#>
[CmdletBinding()]
param(
    [string]$ProjectRoot,
    [string]$OutputDirectory,
    [string]$DataDirectory,
    [switch]$Apply,
    [string]$PlanPath,
    [switch]$Rollback,
    [switch]$Observe,
    [string]$ReceiptPath,
    [ValidateRange(60,600)][int]$GuardSeconds = 120
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:BBScriptPath = $PSCommandPath
$script:BBNames = [ordered]@{
    'Bellomberg-PriceUpdater' = @('run_price_updater.bat','price_updater.log')
    'Bellomberg-NewsFeed' = @('run_news_feed.bat','news_feed.log')
    'Bellomberg-Briefing' = @('run_briefing_v2.bat','briefing.log')
    'Bellomberg-AutoBackup' = @('run_db_backup.bat','backup.log')
    'Bellomberg-Consigliere' = @('run_consigliere_scheduled.bat','scheduler.log')
}

function Get-BBNow { Get-Date }
function Test-BBAdministrator {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}
function Get-BBTextHash([string]$Text) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text)))).Replace('-','').ToLowerInvariant() }
    finally { $sha.Dispose() }
}
function ConvertTo-BBXml([string]$Text) {
    $xml = New-Object Xml.XmlDocument
    $xml.PreserveWhitespace = $false
    $xml.XmlResolver = $null
    $xml.LoadXml($Text)
    if ($xml.DocumentElement.LocalName -ne 'Task' -or
        $xml.DocumentElement.NamespaceURI -ne 'http://schemas.microsoft.com/windows/2004/02/mit/task') {
        throw 'INVALID_TASK_XML: XML Task Scheduler atteso.'
    }
    return ,$xml
}
function Get-BBXmlHash([string]$Text, [switch]$WithoutActions) {
    $xml = ConvertTo-BBXml $Text
    if ($WithoutActions) {
        $actions = $xml.SelectSingleNode('/*[local-name()="Task"]/*[local-name()="Actions"]')
        if ($null -eq $actions) { throw 'INVALID_ACTIONS: sezione Actions assente.' }
        [void]$actions.ParentNode.RemoveChild($actions)
    }
    Add-Type -AssemblyName System.Security
    $canonical = New-Object Security.Cryptography.Xml.XmlDsigC14NTransform
    $canonical.LoadInput($xml)
    $stream = $canonical.GetOutput([IO.Stream])
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-','').ToLowerInvariant() }
    finally { $stream.Dispose(); $sha.Dispose() }
}
function Resolve-BBPath([string]$Path, [switch]$MayNotExist) {
    if ([string]::IsNullOrWhiteSpace($Path)) { throw 'PATH_REQUIRED: percorso assente.' }
    $full = [IO.Path]::GetFullPath($Path)
    if (-not (Test-Path -LiteralPath $full)) {
        if (-not $MayNotExist) { throw "PATH_MISSING: $full" }
        $parent = Split-Path -Parent $full
        if ($parent -eq $full) { throw "PATH_MISSING: $full" }
        return Join-Path (Resolve-BBPath $parent -MayNotExist) (Split-Path -Leaf $full)
    }
    if (-not ('BBSchedulerFinalPath' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
public static class BBSchedulerFinalPath {
 [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
 static extern SafeFileHandle CreateFile(string name, uint access, uint share, IntPtr security, uint creation, uint flags, IntPtr template);
 [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
 static extern uint GetFinalPathNameByHandle(SafeFileHandle handle, StringBuilder path, uint size, uint flags);
 public static string Resolve(string path) {
  using(var h=CreateFile(path,0,7,IntPtr.Zero,3,0x02000000,IntPtr.Zero)) {
   if(h.IsInvalid) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
   var b=new StringBuilder(32768); var n=GetFinalPathNameByHandle(h,b,(uint)b.Capacity,0);
   if(n==0 || n>=b.Capacity) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
   var result=b.ToString();
   if(result.StartsWith(@"\\?\UNC\")) return @"\\"+result.Substring(8);
   return result.StartsWith(@"\\?\") ? result.Substring(4) : result;
  }
 }
}
'@
    }
    return [BBSchedulerFinalPath]::Resolve($full).TrimEnd('\')
}
function Assert-BBPrivateDirectory([string]$Directory,[string]$Root,[string]$Data) {
    $real = Resolve-BBPath $Directory -MayNotExist
    foreach ($excluded in @($Root,$Data)) {
        if ($real -eq $excluded -or $real.StartsWith($excluded.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)) {
            throw 'PRIVATE_OUTPUT_REQUIRED: ricevute e XML devono restare fuori da progetto e directory dati.'
        }
    }
    return $real
}
function Get-BBAction([string]$Xml) {
    $doc = ConvertTo-BBXml $Xml
    $actions = $doc.SelectSingleNode('/*[local-name()="Task"]/*[local-name()="Actions"]')
    if ($null -eq $actions -or $actions.ChildNodes.Count -ne 1 -or $actions.FirstChild.LocalName -ne 'Exec') {
        throw 'UNSUPPORTED_ACTIONS: serve una sola azione Exec esistente.'
    }
    $exec = $actions.FirstChild
    $values = [ordered]@{}
    foreach ($pair in @(@('Execute','Command'),@('Arguments','Arguments'),@('WorkingDirectory','WorkingDirectory'))) {
        $node = $exec.SelectSingleNode('*[local-name()="'+$pair[1]+'"]')
        if ($null -eq $node) { throw "UNSUPPORTED_ACTIONS: manca $($pair[1])." }
        $values[$pair[0]] = $node.InnerText
    }
    $values['Id'] = $exec.GetAttribute('id')
    return [pscustomobject]$values
}
function Get-BBDesiredAction([string]$Root,[string]$Name,[string]$Id) {
    if (-not $script:BBNames.Contains($Name)) { throw 'UNKNOWN_TASK: task fuori whitelist.' }
    [pscustomobject][ordered]@{
        Execute = Resolve-BBPath (Join-Path $env:SystemRoot 'System32\wscript.exe')
        Arguments = '//B //Nologo "{0}" "{1}"' -f (Join-Path $Root 'tools\ops\windows\run_hidden.vbs'), (Join-Path $Root $script:BBNames[$Name][0])
        WorkingDirectory = $Root
        Id = $Id
    }
}
function Test-BBActionsEqual($Left,$Right) {
    return $Left.Execute -ceq $Right.Execute -and $Left.Arguments -ceq $Right.Arguments -and
        $Left.WorkingDirectory -ceq $Right.WorkingDirectory -and $Left.Id -ceq $Right.Id
}
function Assert-BBOriginalAction($Action,[string]$Root,[string]$Name) {
    $desired = Get-BBDesiredAction $Root $Name $Action.Id
    if (Test-BBActionsEqual $Action $desired) { return }
    $cmd = (Get-Command cmd.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
    if ($Action.Execute -ine 'cmd.exe' -and (Resolve-BBPath $Action.Execute) -ine (Resolve-BBPath $cmd)) {
        throw "UNSUPPORTED_INTERPRETER: $Name non usa il cmd.exe atteso."
    }
    if ($Action.Arguments -notmatch '^/c\s+"([^"\r\n]+)"\s*$') {
        throw "UNSUPPORTED_ARGUMENTS: $Name non ha il solo wrapper atteso."
    }
    $batch = $Matches[1]
    if ((Resolve-BBPath $batch) -ine (Resolve-BBPath (Join-Path $Root $script:BBNames[$Name][0])) -or
        (Resolve-BBPath $Action.WorkingDirectory) -ine $Root) {
        throw "UNEXPECTED_WRAPPER: $Name non appartiene al progetto indicato."
    }
}
function Get-BBExpectedXml([string]$Xml,$Action) {
    $doc = ConvertTo-BBXml $Xml
    $exec = $doc.SelectSingleNode('//*[local-name()="Actions"]/*[local-name()="Exec"]')
    foreach ($pair in @(@('Execute','Command'),@('Arguments','Arguments'),@('WorkingDirectory','WorkingDirectory'))) {
        $exec.SelectSingleNode('*[local-name()="'+$pair[1]+'"]').'InnerText' = [string]$Action.($pair[0])
    }
    return $doc.OuterXml
}
function Get-BBEnvironment {
    $vars = [ordered]@{}
    foreach ($name in @('Path','ComSpec','SystemRoot','TEMP','TMP','BELLOMBERG_PROJECT_ROOT','BELLOMBERG_DATA_DIR','PYTHONPATH','PYTHONHOME')) {
        $vars[$name] = [Environment]::GetEnvironmentVariable($name,'Process')
    }
    [pscustomobject][ordered]@{
        Machine = [Environment]::MachineName
        Cmd = (Get-Command cmd.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
        Python = (Get-Command python.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
        VariablesHash = Get-BBTextHash ($vars | ConvertTo-Json -Depth 5 -Compress)
    }
}
function Get-BBInputs([string]$Root) {
    $paths = @($script:BBScriptPath,(Join-Path $Root 'tools\ops\windows\run_hidden.vbs'))
    foreach ($entry in $script:BBNames.Values) {
        $paths += Join-Path $Root $entry[0]
        $paths += Join-Path $Root ('tools\ops\windows\'+$entry[0])
    }
    $paths += Join-Path $env:SystemRoot 'System32\wscript.exe'
    $paths += (Get-Command cmd.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
    $result = foreach ($file in $paths) {
        $real = Resolve-BBPath $file
        [pscustomobject]@{Path=$real;Sha256=(Get-FileHash -LiteralPath $real -Algorithm SHA256).Hash.ToLowerInvariant()}
    }
    return ,@($result)
}
function Get-BBLog([string]$Data,[string]$Name) {
    $file = Join-Path $Data $script:BBNames[$Name][1]
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
        return [pscustomobject]@{Path=$file;Exists=$false;Bytes=$null;LastWriteUtc=$null}
    }
    $item = Get-Item -LiteralPath $file
    return [pscustomobject]@{Path=$file;Exists=$true;Bytes=$item.Length;LastWriteUtc=$item.LastWriteTimeUtc.ToString('o')}
}
function Get-BBTaskSnapshot([string]$Name,[string]$Data) {
    $task = Get-ScheduledTask -TaskName $Name -TaskPath '\' -ErrorAction Stop
    $text = [string](Export-ScheduledTask -TaskName $Name -TaskPath '\' -ErrorAction Stop)
    $info = Get-ScheduledTaskInfo -TaskName $Name -TaskPath '\' -ErrorAction Stop
    $doc = ConvertTo-BBXml $text
    $enabled = $doc.SelectSingleNode('/*[local-name()="Task"]/*[local-name()="Settings"]/*[local-name()="Enabled"]')
    [pscustomobject]@{
        TaskName=$Name;TaskPath='\';State=[string]$task.State;Xml=$text
        XmlHash=(Get-BBXmlHash $text);OutsideActionsHash=(Get-BBXmlHash $text -WithoutActions)
        Enabled=($null -eq $enabled -or $enabled.InnerText -ine 'false')
        LastRunTime=([datetime]$info.LastRunTime).ToString('o');LastTaskResult=$info.LastTaskResult
        NextRunTime=([datetime]$info.NextRunTime).ToString('o');Log=(Get-BBLog $Data $Name)
    }
}
function Get-BBGuard($Snapshot,[int]$Seconds) {
    $reasons = @()
    $now = Get-BBNow
    if ($Snapshot.State -notin @('Ready','Disabled')) { $reasons += 'TASK_NOT_IDLE' }
    if ($Snapshot.TaskName -eq 'Bellomberg-Consigliere' -and ($Snapshot.Enabled -or $Snapshot.State -ne 'Disabled')) {
        $reasons += 'CONSIGLIERE_ENABLED'
    }
    if ($Snapshot.Enabled) {
        $next = [datetime]$Snapshot.NextRunTime
        if ($next.Year -le 2000 -or $next -lt $now.AddSeconds(-$Seconds)) { $reasons += 'NEXT_TRIGGER_UNKNOWN_OR_PAST' }
        if ($next.Year -gt 2000 -and [Math]::Abs(($next-$now).TotalSeconds) -le $Seconds) { $reasons += 'NEAR_NEXT_TRIGGER' }
        $last = [datetime]$Snapshot.LastRunTime
        if ($last.Year -gt 2000 -and [Math]::Abs(($now-$last).TotalSeconds) -le $Seconds) { $reasons += 'NEAR_LAST_RUN' }
    }
    [pscustomobject]@{TaskName=$Snapshot.TaskName;At=$now.ToString('o');GuardSeconds=$Seconds;Safe=($reasons.Count -eq 0);Reasons=$reasons}
}
function Write-BBArtifact([string]$Path,$Value) {
    $json = $Value | ConvertTo-Json -Depth 40
    $utf8 = New-Object Text.UTF8Encoding($false)
    $temporary = $Path+'.writing'
    [IO.File]::WriteAllText($temporary,$json,$utf8)
    Move-Item -LiteralPath $temporary -Destination $Path -Force
    [IO.File]::WriteAllText($Path+'.sha256',(Get-BBTextHash $json),$utf8)
}
function Read-BBArtifact([string]$Path) {
    $text = [IO.File]::ReadAllText((Resolve-BBPath $Path))
    $expected = [IO.File]::ReadAllText((Resolve-BBPath ($Path+'.sha256'))).Trim()
    if ((Get-BBTextHash $text) -cne $expected) { throw 'ARTIFACT_HASH_MISMATCH: ricevuta/piano modificato.' }
    return $text | ConvertFrom-Json
}
function Assert-BBPlan($Plan,[string]$Root,[string]$Path,[switch]$ForApply) {
    if ($Plan.Schema -ne 1 -or $Plan.ProjectRoot -ine $Root) { throw 'PLAN_IDENTITY_MISMATCH: progetto/schema diverso.' }
    [void](Assert-BBPrivateDirectory (Split-Path -Parent $Path) $Root $Plan.DataDirectory)
    if (@($Plan.Tasks).Count -ne $script:BBNames.Count -or
        (@($Plan.Tasks.TaskName | Sort-Object) -join '|') -cne (@($script:BBNames.Keys | Sort-Object) -join '|')) {
        throw 'PLAN_TASKS_MISMATCH: whitelist differente.'
    }
    foreach ($entry in $Plan.Tasks) {
        $expectedPath = Join-Path (Split-Path -Parent $Path) ($entry.TaskName+'.xml')
        if ($entry.XmlPath -ine $expectedPath -or $entry.TaskPath -ne '\') { throw 'PLAN_XML_PATH_MISMATCH' }
        $xml = [IO.File]::ReadAllText((Resolve-BBPath $entry.XmlPath))
        if ((Get-BBXmlHash $xml) -cne $entry.BeforeHash) { throw 'PLAN_XML_HASH_MISMATCH' }
        $before = Get-BBAction $xml
        Assert-BBOriginalAction $before $Root $entry.TaskName
        $after = Get-BBDesiredAction $Root $entry.TaskName $before.Id
        if (-not (Test-BBActionsEqual $before $entry.BeforeAction) -or -not (Test-BBActionsEqual $after $entry.AfterAction) -or
            (Get-BBXmlHash (Get-BBExpectedXml $xml $after)) -cne $entry.AfterHash -or
            (Get-BBXmlHash $xml -WithoutActions) -cne $entry.OutsideActionsHash) { throw 'PLAN_ACTION_MISMATCH' }
    }
    if ($ForApply) {
        $age = ((Get-BBNow)-[datetime]$Plan.CreatedAt).TotalMinutes
        if ($age -lt 0 -or $age -gt 30) { throw 'PLAN_EXPIRED: validita 30 minuti; creare una nuova anteprima.' }
        if ($Plan.Status -ne 'Ready' -or $Plan.GuardSeconds -lt 60 -or $Plan.GuardSeconds -gt 600) { throw 'PLAN_UNSAFE' }
        $envNow = Get-BBEnvironment
        if (($envNow | ConvertTo-Json -Compress) -cne ($Plan.Environment | ConvertTo-Json -Compress)) { throw 'ENVIRONMENT_DRIFT: interprete/ambiente diverso dal piano.' }
        $inputsNow = Get-BBInputs $Root
        if (($inputsNow | ConvertTo-Json -Compress) -cne ($Plan.Inputs | ConvertTo-Json -Compress)) { throw 'SOURCE_DRIFT: launcher/wrapper/interpreti modificati.' }
    }
}
function Set-BBAction([string]$Name,$Action) {
    $arguments = @{Execute=$Action.Execute;Argument=$Action.Arguments;WorkingDirectory=$Action.WorkingDirectory}
    if ($Action.Id) { $arguments['Id']=$Action.Id }
    $newAction = New-ScheduledTaskAction @arguments
    $script:BBMutationCalls++
    Set-ScheduledTask -TaskName $Name -TaskPath '\' -Action $newAction -ErrorAction Stop | Out-Null
}
function Restore-BBActions($Entries,[string]$Data,[int]$GuardSeconds) {
    $restored=@();$blocked=@()
    foreach ($entry in @($Entries)) {
        try {
            $current = Get-BBTaskSnapshot $entry.TaskName $Data
            if ($current.XmlHash -ceq $entry.BeforeHash) { continue }
            if ($current.XmlHash -cne $entry.AfterHash) { throw 'ROLLBACK_DRIFT: XML non riconoscibile; non sovrascrivo modifiche concorrenti.' }
            $guard = Get-BBGuard $current $GuardSeconds
            if (-not $guard.Safe) { throw ('ROLLBACK_UNSAFE: '+($guard.Reasons -join ',')) }
            Set-BBAction $entry.TaskName $entry.BeforeAction
            $checked = Get-BBTaskSnapshot $entry.TaskName $Data
            if ($checked.XmlHash -cne $entry.BeforeHash) { throw 'ROLLBACK_VERIFY_FAILED' }
            $restored += $entry.TaskName
        } catch { $blocked += [pscustomobject]@{TaskName=$entry.TaskName;Reason=$_.Exception.Message} }
    }
    return [pscustomobject]@{Restored=$restored;Blocked=$blocked}
}

function Invoke-BBTaskTransition {
    [CmdletBinding()]
    param([string]$ProjectRoot,[string]$OutputDirectory,[string]$DataDirectory,
          [switch]$Apply,[string]$PlanPath,[switch]$Rollback,[switch]$Observe,[string]$ReceiptPath,
          [ValidateRange(60,600)][int]$GuardSeconds=120)
    $script:BBMutationCalls=0
    if (([int][bool]$Apply+[int][bool]$Rollback+[int][bool]$Observe) -gt 1) { throw 'MODE_CONFLICT' }
    $root=Resolve-BBPath $ProjectRoot
    if ($Apply -or $Rollback) {
        if (-not (Test-BBAdministrator)) { throw 'ADMIN_REQUIRED: serve PowerShell elevato per modificare Actions.' }
    }
    if (-not $Apply -and -not $Rollback -and -not $Observe) {
        if ($PlanPath -or $ReceiptPath) { throw 'UNEXPECTED_ARTIFACT_ARGUMENT' }
        $data = if ($DataDirectory) {Resolve-BBPath $DataDirectory} else {Resolve-BBPath (Join-Path $root 'data')}
        $out=Assert-BBPrivateDirectory $OutputDirectory $root $data
        if (Test-Path -LiteralPath $out) { throw 'OUTPUT_EXISTS: usare una nuova directory privata.' }
        [void][IO.Directory]::CreateDirectory($out)
        $out=Assert-BBPrivateDirectory $out $root $data
        $entries=@();$guards=@();$snapshots=@()
        foreach ($name in $script:BBNames.Keys) {
            $snapshot=Get-BBTaskSnapshot $name $data
            $before=Get-BBAction $snapshot.Xml
            Assert-BBOriginalAction $before $root $name
            $after=Get-BBDesiredAction $root $name $before.Id
            $xmlPath=Join-Path $out ($name+'.xml')
            [IO.File]::WriteAllText($xmlPath,$snapshot.Xml,[Text.Encoding]::Unicode)
            if ((Get-BBXmlHash ([IO.File]::ReadAllText($xmlPath))) -cne $snapshot.XmlHash) { throw 'XML_BACKUP_VERIFY_FAILED' }
            $entries += [pscustomobject]@{TaskName=$name;TaskPath='\';XmlPath=$xmlPath;BeforeHash=$snapshot.XmlHash;
                OutsideActionsHash=$snapshot.OutsideActionsHash;AfterHash=(Get-BBXmlHash (Get-BBExpectedXml $snapshot.Xml $after));
                BeforeAction=$before;AfterAction=$after}
            $snapshots+=$snapshot;$guards+=Get-BBGuard $snapshot $GuardSeconds
        }
        foreach ($snapshot in $snapshots) {
            $afterRead=Get-BBTaskSnapshot $snapshot.TaskName $data
            if ($afterRead.XmlHash -cne $snapshot.XmlHash) { throw 'DRY_RUN_DRIFT: definizione cambiata durante la lettura.' }
        }
        $plan=[pscustomobject]@{Schema=1;Status=$(if (@($guards | Where-Object {-not $_.Safe}).Count) {'Unsafe'} else {'Ready'});
            CreatedAt=(Get-BBNow).ToString('o');ValidForMinutes=30;ProjectRoot=$root;DataDirectory=$data;
            GuardSeconds=$GuardSeconds;Environment=(Get-BBEnvironment);Inputs=(Get-BBInputs $root);
            MutationCalls=$script:BBMutationCalls;Tasks=$entries;Guards=$guards;Baseline=$snapshots}
        $path=Join-Path $out 'plan.json';Write-BBArtifact $path $plan
        if ($plan.Status -ne 'Ready') { throw "PLAN_UNSAFE: guardie non superate; dettagli in $path" }
        return [pscustomobject]@{Mode='Plan';PlanPath=$path;MutationCalls=$script:BBMutationCalls;Tasks=$entries.Count;ValidForMinutes=30;GuardSeconds=$GuardSeconds}
    }
    if ($Apply) {
        $path=Resolve-BBPath $PlanPath;$plan=Read-BBArtifact $path
        Assert-BBPlan $plan $root $path -ForApply
        $baseline=@()
        foreach ($entry in $plan.Tasks) {
            $current=Get-BBTaskSnapshot $entry.TaskName $plan.DataDirectory
            if ($current.XmlHash -cne $entry.BeforeHash) { throw "TASK_DRIFT: $($entry.TaskName) cambiato dopo il piano." }
            $guard=Get-BBGuard $current $plan.GuardSeconds
            if (-not $guard.Safe) { throw ('APPLY_UNSAFE: '+$entry.TaskName+' '+($guard.Reasons -join ',')) }
            $baseline+=$current
        }
        $receiptPath=Join-Path (Split-Path -Parent $path) ('receipt-'+[guid]::NewGuid().ToString('N')+'.json')
        $receipt=[pscustomobject]@{Schema=1;Status='Applying';PlanPath=$path;PlanHash=(Get-FileHash -LiteralPath $path).Hash;
            At=(Get-BBNow).ToString('o');ChangedTasks=@();AttemptedTasks=@();MutationCalls=0;Baseline=$baseline;Rollback=$null;Error=$null}
        Write-BBArtifact $receiptPath $receipt
        $attempted=@()
        try {
            foreach ($entry in $plan.Tasks) {
                if ($entry.BeforeHash -ceq $entry.AfterHash) { continue }
                $current=Get-BBTaskSnapshot $entry.TaskName $plan.DataDirectory
                if ($current.XmlHash -cne $entry.BeforeHash) { throw "TASK_DRIFT: $($entry.TaskName) prima della scrittura." }
                $guard=Get-BBGuard $current $plan.GuardSeconds
                if (-not $guard.Safe) { throw ('APPLY_UNSAFE: '+($guard.Reasons -join ',')) }
                $attempted+=$entry;$receipt.AttemptedTasks+= $entry.TaskName
                Write-BBArtifact $receiptPath $receipt
                Set-BBAction $entry.TaskName $entry.AfterAction
                $checked=Get-BBTaskSnapshot $entry.TaskName $plan.DataDirectory
                if ($checked.XmlHash -cne $entry.AfterHash -or $checked.OutsideActionsHash -cne $entry.OutsideActionsHash) { throw 'APPLY_VERIFY_FAILED: XML diverso dalla sola sostituzione Actions.' }
                if ($entry.TaskName -eq 'Bellomberg-Consigliere' -and ($checked.Enabled -or $checked.State -ne 'Disabled')) { throw 'CONSIGLIERE_ENABLED' }
                $receipt.ChangedTasks+=$entry.TaskName;$receipt.MutationCalls=$script:BBMutationCalls
                Write-BBArtifact $receiptPath $receipt
            }
            # Recheck the whole batch: an earlier task may have drifted while a
            # later action was being changed. Do not certify that as Applied.
            foreach ($entry in $plan.Tasks) {
                $final=Get-BBTaskSnapshot $entry.TaskName $plan.DataDirectory
                if ($final.XmlHash -cne $entry.AfterHash) { throw ('FINAL_BATCH_DRIFT: '+$entry.TaskName) }
                if ($entry.TaskName -eq 'Bellomberg-Consigliere' -and ($final.Enabled -or $final.State -ne 'Disabled')) { throw 'CONSIGLIERE_ENABLED' }
            }
            $receipt.Status='Applied';Write-BBArtifact $receiptPath $receipt
        } catch {
            $reason=$_.Exception.Message
            [array]::Reverse($attempted)
            $receipt.Rollback=Restore-BBActions $attempted $plan.DataDirectory $plan.GuardSeconds
            $receipt.Status=if ($receipt.Rollback.Blocked.Count) {'RollbackIncomplete'} else {'RestoredAfterFailure'}
            $receipt.Error=$reason;$receipt.MutationCalls=$script:BBMutationCalls
            Write-BBArtifact $receiptPath $receipt
            throw "APPLY_FAILED: $reason Ricevuta: $receiptPath; rollback: $($receipt.Status)."
        }
        return [pscustomobject]@{Mode='Apply';ReceiptPath=$receiptPath;MutationCalls=$script:BBMutationCalls;ChangedTasks=$receipt.ChangedTasks}
    }
    $receiptFile=Resolve-BBPath $ReceiptPath;$receipt=Read-BBArtifact $receiptFile
    $plan=Read-BBArtifact $receipt.PlanPath
    if ($receipt.Schema -ne 1) { throw 'RECEIPT_SCHEMA_MISMATCH' }
    [void](Assert-BBPrivateDirectory (Split-Path -Parent $receiptFile) $root $plan.DataDirectory)
    if ((Get-FileHash -LiteralPath $receipt.PlanPath).Hash -cne $receipt.PlanHash) { throw 'RECEIPT_PLAN_HASH_MISMATCH' }
    Assert-BBPlan $plan $root $receipt.PlanPath
    if ($receipt.Status -notin @('Applied','Applying','RollbackIncomplete')) { throw 'RECEIPT_NOT_APPLIED' }
    if ($Rollback) {
        $entries=@($plan.Tasks | Where-Object {$_.TaskName -in $receipt.AttemptedTasks})
        foreach ($entry in $entries) {
            $current=Get-BBTaskSnapshot $entry.TaskName $plan.DataDirectory
            if ($current.XmlHash -cne $entry.AfterHash -and $current.XmlHash -cne $entry.BeforeHash) { throw 'ROLLBACK_DRIFT: almeno una definizione e cambiata; nessuna modifica applicata.' }
            $guard=Get-BBGuard $current $plan.GuardSeconds
            if (-not $guard.Safe) { throw ('ROLLBACK_UNSAFE: '+($guard.Reasons -join ',')) }
        }
        [array]::Reverse($entries)
        $restoration=Restore-BBActions $entries $plan.DataDirectory $plan.GuardSeconds
        $rollbackPath=Join-Path (Split-Path -Parent $receiptFile) ('rollback-'+[guid]::NewGuid().ToString('N')+'.json')
        Write-BBArtifact $rollbackPath ([pscustomobject]@{Schema=1;At=(Get-BBNow).ToString('o');ReceiptPath=$receiptFile;
            Status=$(if ($restoration.Blocked.Count) {'Incomplete'} else {'Restored'});Result=$restoration;MutationCalls=$script:BBMutationCalls})
        if ($restoration.Blocked.Count) { throw "ROLLBACK_INCOMPLETE: vedere $rollbackPath" }
        return [pscustomobject]@{Mode='Rollback';ReceiptPath=$rollbackPath;MutationCalls=$script:BBMutationCalls;Restored=$restoration.Restored}
    }
    $observations=@()
    foreach ($entry in $plan.Tasks) {
        $before=@($receipt.Baseline | Where-Object {$_.TaskName -eq $entry.TaskName})[0]
        $after=Get-BBTaskSnapshot $entry.TaskName $plan.DataDirectory
        $observations+=[pscustomobject]@{TaskName=$entry.TaskName;State=$after.State;
            BeforeLastRun=$before.LastRunTime;AfterLastRun=$after.LastRunTime;LastTaskResult=$after.LastTaskResult;NextRunTime=$after.NextRunTime;
            NewRun=([datetime]$after.LastRunTime -gt [datetime]$before.LastRunTime);
            BeforeLogBytes=$before.Log.Bytes;AfterLogBytes=$after.Log.Bytes;
            LogDeltaBytes=$(if ($before.Log.Exists -and $after.Log.Exists) {$after.Log.Bytes-$before.Log.Bytes} else {$null});
            DefinitionMatchesApplied=($after.XmlHash -ceq $entry.AfterHash);WindowObservation='PM_REQUIRED'}
    }
    $observationPath=Join-Path (Split-Path -Parent $receiptFile) ('observation-'+[guid]::NewGuid().ToString('N')+'.json')
    Write-BBArtifact $observationPath ([pscustomobject]@{Schema=1;At=(Get-BBNow).ToString('o');ReceiptPath=$receiptFile;
        MutationCalls=$script:BBMutationCalls;Observations=$observations;WindowObservation='PM_REQUIRED'})
    return [pscustomobject]@{Mode='Observe';ObservationPath=$observationPath;MutationCalls=$script:BBMutationCalls;Observations=$observations}
}

# Dot sourcing exposes the same core for isolated cmdlet-fake tests; no test bypass
# exists in the command-line interface and no task is touched on import.
if ($MyInvocation.InvocationName -ne '.') {
    try {
        Invoke-BBTaskTransition @PSBoundParameters | ConvertTo-Json -Depth 30
        exit 0
    } catch {
        [Console]::Error.WriteLine($_.Exception.Message)
        exit 1
    }
}
