<#
    terminal.ps1 — one command, from Windows.

    Starts the agent service in WSL if nothing is listening yet, then attaches this terminal to it.
    The service is started DETACHED on purpose: closing this window leaves it running, and Unity
    stays connected. Run this again to reattach.

    It does NOT outlive the run. Stopping play mode in Unity closes the terminal and the service
    together, so the next run starts from here again — which takes about two seconds. That holds
    because Unity launches this WITHOUT -NoExit: the service exits when the engine says play mode
    ended, the client follows it, this script returns, and the window goes with it. A window still
    on screen therefore means a failure worth reading, not a run that finished.

        powershell -ExecutionPolicy Bypass -File \\wsl.localhost\Ubuntu-24.04\home\yuq8cp\Research\animation_agent\terminal.ps1

    -Restart stops a running service first, which is what you want after changing agent code.
#>
param(
    [string]$Distro = "Ubuntu-24.04",
    [string]$Repo = "~/Research/animation_agent",
    [string]$Python = "~/miniforge3/envs/animation-agent/bin/python",
    [int]$Port = 8771,
    [switch]$Restart
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

function Test-Service {
    try {
        $c = New-Object Net.Sockets.TcpClient
        $c.Connect("127.0.0.1", $Port)
        $up = $c.Connected
        $c.Close()
        return $up
    } catch { return $false }
}

# The window closes with this script now, so a failure that just returned would take its own
# explanation with it. Unity launches this without -NoExit precisely so a normal end tidies up; the
# wait here is what keeps the abnormal one on screen.
function Fail($message) {
    Write-Host $message -ForegroundColor Red
    Write-Host ""
    Read-Host "press Enter to close"
    exit 1
}

if ($Restart -and (Test-Service)) {
    Write-Host "stopping the running service" -ForegroundColor DarkGray
    # The bracket stops the pattern matching the `bash -lc` that carries it. Spelled 'cli.py --engine'
    # the shell's own command line matches, so pkill kills its own parent and the `|| true` never runs.
    wsl.exe -d $Distro -- bash -lc "pkill -f '[c]li.py --engine' || true"
    Start-Sleep -Milliseconds 600
}

if (-not (Test-Service)) {
    Write-Host "starting the agent service in $Distro (detached)" -ForegroundColor DarkGray

    # ABSOLUTE PATHS, AND NO SHELL SYNTAX IN THE LAUNCH ARGUMENTS.
    #
    # This used to hand `bash -lc "cd $Repo && exec $Python cli.py --engine --headless"` to
    # Start-Process, and it silently did not work: the service never came up, and the log showed
    # python looking for `cli.py` under `/mnt/d/...`, which is Unity's working directory translated
    # into the distro. The `cd` reported success and did not change directory, so `&&` carried on
    # regardless and the launch failed somewhere no window was showing. Pressing Play gave a terminal
    # attached to nothing.
    #
    # Reproduced from a file, so it is not an artefact of how the line was typed; the same `cd` works
    # when wsl.exe is invoked directly rather than through Start-Process. The argument array is the
    # part that differs, so nothing here relies on it any more: `~` is expanded up front by a shell
    # that works, the working directory is set by wsl's own `--cd` instead of a `cd` inside the
    # command, and what reaches bash is one path plus flags, with no `&&`, no `~` and no quoting for
    # PowerShell to rebuild.
    $repoAbs = (& wsl.exe -d $Distro -- bash -lc "cd $Repo && pwd").Trim()
    $pythonAbs = (& wsl.exe -d $Distro -- bash -lc "readlink -f $Python").Trim()
    if (-not $repoAbs -or -not $pythonAbs) {
        Fail "could not resolve the repo ($Repo) or python ($Python) inside $Distro"
    }

    # PLAIN TOKENS, NO SHELL SYNTAX. Every element here is one word: no spaces, no quotes, no
    # redirection, nothing for PowerShell to take apart and put back together differently. An earlier
    # attempt to capture the service's output by adding `bash -c "... > log 2>&1"` to this array
    # reintroduced exactly the failure this shape exists to avoid -- the log was never even created.
    # The service's output is captured in the failure path below instead, where the call is direct.
    Start-Process -WindowStyle Hidden -FilePath "wsl.exe" -ArgumentList @(
        "-d", $Distro, "--cd", $repoAbs, "--",
        "env", "MOTIONKB_DIR=/mnt/d/Research/AI_agent/Animation_agent/Animation/agent/animation_knowledge_base",
        $pythonAbs, "-u", "cli.py", "--engine", "--headless"
    )
    # LONG ENOUGH FOR A COLD START. Ten seconds was fine against a warm distro and is not a fair
    # budget from a stopped one: the VM boots, python starts, and the knowledge base is read across
    # /mnt/d, where DrvFs makes every one of those files cost. Waiting is free; a wait that expires
    # while the service is still coming up costs the whole run and reads like a failure.
    $waited = 0
    while ($waited -lt 45 -and -not (Test-Service)) {
        Start-Sleep -Milliseconds 250
        $waited += 0.25
        # Cold starts look identical to hangs without this, and the last one was reported as a hang.
        if ($waited -eq 8) { Write-Host "  still starting (cold distro takes a moment)" -ForegroundColor DarkGray }
    }
    if (-not (Test-Service)) {
        # ASK IT AGAIN, IN THE FOREGROUND, AND KEEP WHAT IT SAYS. The detached launch is hidden, so a
        # service that dies on the way up leaves nothing behind and the wait above is all anyone sees.
        # This call is direct rather than through Start-Process, which is the difference that makes a
        # command string safe to pass, so it can carry the timeout and the redirect the launch cannot.
        Write-Host ""
        Write-Host "asking it again in the foreground, to see what it says:" -ForegroundColor DarkGray
        $said = (& wsl.exe -d $Distro -- bash -lc "cd '$repoAbs' && MOTIONKB_DIR=/mnt/d/Research/AI_agent/Animation_agent/Animation/agent/animation_knowledge_base timeout 20 '$pythonAbs' -u cli.py --engine --headless 2>&1 | tail -20")
        if ($said) { $said | ForEach-Object { Write-Host "  $_" } }
        else { Write-Host "  nothing at all - it did not get far enough to say anything" }
        Fail "the service did not come up on port $Port after $waited s."
    }
}

$client = Join-Path $here "terminal.py"
$py = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $py) { Fail "no python on PATH (Windows side)" }
& $py.Source $client --port $Port
