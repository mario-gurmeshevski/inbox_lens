param(
    [Parameter(Position=0)]
    [string]$Task,

    [Parameter(ValueFromRemainingArguments)]
    [string[]]$ExtraArgs
)

$ErrorActionPreference = "Continue"

if (-not $Task) {
    Write-Host "Usage: ./commands.ps1 <task> [args...]"
    Write-Host ""
    Write-Host "Available tasks:"
    Write-Host "  install          Install Python dependencies (project-local .venv)"
    Write-Host "  uninstall        Uninstall Python dependencies"
    Write-Host "  dev-install      Install dev dependencies"
    Write-Host "  web              Run the web server"
    Write-Host "  test             Run tests"
    Write-Host "  test-counts      Show test counts per file"
    Write-Host "  test-cov         Run tests with coverage"
    Write-Host "  lint             Run linters (Ruff for Python, djlint for templates)"
    Write-Host "  lint-fix         Auto-fix Ruff lint issues"
    Write-Host "  format           Format Python (Ruff) and templates (djlint)"
    Write-Host "  unused           Find unused code (Vulture)"
    Write-Host "  pre-commit       Format, lint (incl. Vulture), and test"
    Write-Host "  clean            Remove caches and compiled files"
    Write-Host "  reset            Clean + remove DB and secret key"
    Write-Host "  up               Start Docker Compose (auto-detect host IP)"
    Write-Host "  up-ts            Start Docker Compose with Tailscale"
    Write-Host "  down             Stop and remove Docker Compose"
    Write-Host "  prune            Remove unused Docker images, build cache, stopped containers (no volumes)"
    Write-Host "  prune-auto       Remove dangling images + build cache only (runs automatically after up)"
    Write-Host "  stop             Stop Docker Compose containers (no data removed)"
    Write-Host "  start            Start previously-stopped Docker Compose containers"
    Write-Host "  restart          Restart running Docker Compose containers"
    Write-Host "  tailscale-up     Show Tailscale logs (login URL)"
    Write-Host "  tailscale-status Show Tailscale status"
    Write-Host "  tailscale-ip     Show Tailscale IP"
    Write-Host "  tailscale-logout Logout from Tailscale"
    Write-Host "  purge            Full reset: logout, clean, remove all Docker resources + state"
    exit 0
}

$PYTHON = "python"
$VENV = ".venv"
$VENV_PYTHON = "$VENV\Scripts\python.exe"
$VENV_PIP = "$VENV\Scripts\pip.exe"
$DJLINT = "$VENV\Scripts\djlint.exe"
$RUNTIME_STAMP = "$VENV\.runtime.stamp"
$DEV_STAMP = "$VENV\.dev.stamp"
$COMPOSE_FILES_TS = @("-f", "docker-compose.yaml", "-f", "docker-compose.tailscale.yaml")
$COMPOSE_DEFAULT = @("compose")

$WEB_HOST = if ($env:WEB_HOST) { $env:WEB_HOST } else { "0.0.0.0" }
$WEB_PORT = if ($env:WEB_PORT) { $env:WEB_PORT } else { "8000" }

# Load .env file automatically
if (Test-Path ".env") {
    Get-Content ".env" | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$' -and $_ -notmatch '^\s*#') {
            $key = $Matches[1]
            $val = $Matches[2] -replace '^"|"$', ''
            if (-not (Get-ChildItem Env: | Where-Object { $_.Name -eq $key })) {
                Set-Item -Path "env:$key" -Value $val
            }
        }
    }
}

function Get-HostIP {
    $route = Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($route) {
        $iface = $route.InterfaceIndex
        $ip = (Get-NetIPAddress -InterfaceIndex $iface -AddressFamily IPv4 -ErrorAction SilentlyContinue | Select-Object -First 1).IPAddress
        if ($ip) { return $ip }
    }
    $conn = Get-NetConnectionInformation -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn) { return $conn.LocalAddress }
    $ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -ne '127.0.0.1' } | Select-Object -First 1).IPAddress
    return $ip
}

function Invoke-PruneAuto {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { return }
    $reclaimed = (docker image prune -f 2>$null | ConvertFrom-Json -ErrorAction SilentlyContinue)
    $cacheReclaimed = (docker builder prune -f 2>$null | ConvertFrom-Json -ErrorAction SilentlyContinue)
    $total = 0
    if ($reclaimed -and $reclaimed.SpaceReclaimed) { $total += [int64]$reclaimed.SpaceReclaimed }
    if ($cacheReclaimed -and $cacheReclaimed.SpaceReclaimed) { $total += [int64]$cacheReclaimed.SpaceReclaimed }
    if ($total -gt 0) {
        Write-Host ("Auto-prune reclaimed {0:N0} bytes of dangling images + build cache." -f $total)
    }
}

function Invoke-Clean {
    Get-ChildItem -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Get-ChildItem -Recurse -Directory -Filter '.pytest_cache' | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Get-ChildItem -Recurse -Filter '*.pyc' | Remove-Item -Force -ErrorAction SilentlyContinue
}

function Get-FileMtime {
    param([string]$Path)
    $abs = (Resolve-Path -LiteralPath $Path -ErrorAction SilentlyContinue)
    if (-not $abs) { return $null }
    $fi = [IO.FileInfo]::new($abs.ProviderPath)
    if ($fi.Exists) { return $fi.LastWriteTime } else { return $null }
}

function Test-RuntimeStale {
    if (-not (Test-Path $VENV_PYTHON)) { return $true }
    $rt = Get-FileMtime $RUNTIME_STAMP
    if (-not $rt) { return $true }
    $pin = Get-FileMtime ".python-version"
    if ($pin -and $pin -gt $rt) { return $true }
    return $false
}

function Test-DevStale {
    if (Test-RuntimeStale) { return $true }
    $dv = Get-FileMtime $DEV_STAMP
    if (-not $dv) { return $true }
    if ((Get-FileMtime $RUNTIME_STAMP) -gt $dv) { return $true }
    $pp = Get-FileMtime "pyproject.toml"
    if ($pp -and $pp -gt $dv) { return $true }
    return $false
}

switch ($Task) {
    "install" {
        if (Test-RuntimeStale) {
            & $PYTHON -m venv $VENV
            Set-Content -Path $RUNTIME_STAMP -Value $null
        }
        & $VENV_PIP install -e .
        Set-Content -Path $DEV_STAMP -Value $null
    }
    "uninstall" {
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $VENV
    }
    "dev-install" {
        if (Test-RuntimeStale) {
            & $PYTHON -m venv $VENV
            Set-Content -Path $RUNTIME_STAMP -Value $null
        }
        if (Test-DevStale) {
            & $VENV_PIP install -e ".[dev]"
            Set-Content -Path $DEV_STAMP -Value $null
        }
    }
    "web" { & $VENV_PYTHON -m uvicorn src.scripts.web:app --host $WEB_HOST --port $WEB_PORT --reload }
    "test" { & $VENV_PYTHON -m pytest src/tests/ -v }
    "test-counts" {
        $results = @()
        $files = Get-ChildItem "src/tests/test_*.py" | Sort-Object Name
        foreach ($f in $files) {
            $output = & $VENV_PYTHON -m pytest $f.FullName --collect-only 2>&1
            if ($output -match 'collected (\d+)') {
                $count = [int]$Matches[1]
                $results += [pscustomobject]@{ Name = $f.Name; Count = $count }
            }
        }
        $results | Sort-Object Count -Descending | ForEach-Object {
            Write-Host "$($_.Name): $($_.Count)"
        }
    }
    "test-cov" { & $VENV_PYTHON -m pytest src/tests/ --cov=src/scripts --cov-report=term-missing }
    "lint" {
        & $VENV_PYTHON -m ruff check src/
        if ($LASTEXITCODE -ne 0) {
            Write-Host ""
            Write-Host "Run './commands.ps1 lint-fix' to auto-fix Ruff issues." -ForegroundColor Yellow
            exit 1
        }
        & $VENV_PYTHON -m vulture src/scripts --exclude=src/scripts/web.py,src/scripts/auth.py,src/scripts/event_bus.py,src/scripts/idle_monitor.py,src/scripts/outbox_worker.py,src/scripts/updater.py,src/scripts/utils.py
        & $DJLINT --check src/web/templates
    }
    "lint-fix" {
        & $VENV_PYTHON -m ruff check src/ --fix
    }
    "format" {
        & $VENV_PYTHON -m ruff format src/
        & $DJLINT --reformat src/web/templates
    }
    "unused" {
        & $VENV_PYTHON -m vulture src/scripts --exclude=src/scripts/web.py,src/scripts/auth.py,src/scripts/event_bus.py,src/scripts/idle_monitor.py,src/scripts/outbox_worker.py,src/scripts/updater.py,src/scripts/utils.py
    }
    "pre-commit" {
        & $VENV_PYTHON -m ruff format src/
        & $DJLINT --reformat src/web/templates
        & $VENV_PYTHON -m ruff check src/
        if ($LASTEXITCODE -ne 0) {
            Write-Host ""
            Write-Host "💡 Run './commands.ps1 lint-fix' to auto-fix Ruff issues." -ForegroundColor Yellow
            exit 1
        }
        & $VENV_PYTHON -m vulture src/scripts --exclude=src/scripts/web.py,src/scripts/auth.py,src/scripts/event_bus.py,src/scripts/idle_monitor.py,src/scripts/outbox_worker.py,src/scripts/updater.py,src/scripts/utils.py
        & $DJLINT --check src/web/templates
        & $VENV_PYTHON -m pytest src/tests/ -v
    }

    "clean" {
        Invoke-Clean
    }

    "reset" {
        Invoke-Clean
        @("src/data/emails.db", "src/data/emails.db-shm", "src/data/emails.db-wal", "src/data/.secret.key", "src/data/.session.key") | ForEach-Object {
            Remove-Item -Force -ErrorAction SilentlyContinue $_
        }
    }

    "up" {
        $hostIP = Get-HostIP
        Write-Host "Host IP: $hostIP"
        $env:HOST_IP = $hostIP
        $argList = @("compose", "up", "--build")
        if ($ExtraArgs) { $argList += $ExtraArgs }
        & docker @argList
        if ($LASTEXITCODE -eq 0) { Invoke-PruneAuto }
    }

    "up-ts" {
        $argList = @("compose") + $COMPOSE_FILES_TS + @("up", "--build")
        if ($ExtraArgs) { $argList += $ExtraArgs }
        & docker @argList
        if ($LASTEXITCODE -eq 0) { Invoke-PruneAuto }
    }

    "prune-auto" {
        Invoke-PruneAuto
    }

    "prune" {
        Invoke-PruneAuto
        if (Get-Command docker -ErrorAction SilentlyContinue) {
            docker image prune -a -f 2>$null | Out-Null
            docker container prune -f 2>$null | Out-Null
            Write-Host "Deep prune complete."
        }
    }

    "down" {
        docker compose down --rmi local --volumes --remove-orphans
    }

    "stop" {
        $argList = $COMPOSE_DEFAULT + @("stop")
        & docker @argList
    }

    "start" {
        $argList = $COMPOSE_DEFAULT + @("start")
        & docker @argList
    }

    "restart" {
        $argList = $COMPOSE_DEFAULT + @("restart")
        & docker @argList
    }

    "tailscale-up" {
        Write-Host "Tailscale logs (look for login URL on first run):"
        Write-Host "---"
        $argList = @("compose") + $COMPOSE_FILES_TS + @("logs", "tailscale")
        & docker @argList 2>$null
        if ($LASTEXITCODE -ne 0) { Write-Host "Run './commands.ps1 up-ts' first." }
    }

    "tailscale-status" {
        $argList = @("compose") + $COMPOSE_FILES_TS + @("exec", "tailscale", "/usr/local/bin/tailscale", "status")
        & docker @argList
    }

    "tailscale-ip" {
        $argList = @("compose") + $COMPOSE_FILES_TS + @("exec", "tailscale", "/usr/local/bin/tailscale", "ip", "-4")
        $ip = & docker @argList 2>$null
        if ($LASTEXITCODE -eq 0 -and $ip) {
            Write-Host $ip
        } else {
            Write-Host "Tailscale not running. Run './commands.ps1 up-ts' first."
        }
    }

    "tailscale-logout" {
        $argList = @("compose") + $COMPOSE_FILES_TS + @("exec", "tailscale", "/usr/local/bin/tailscale", "logout")
        & docker @argList 2>$null
    }

    "purge" {
        # Logout from Tailscale first
        $logoutArgs = @("compose") + $COMPOSE_FILES_TS + @("exec", "tailscale", "/usr/local/bin/tailscale", "logout")
        & docker @logoutArgs 2>$null

        # Clean cache files
        Invoke-Clean

        # Remove data files
        @("src/data/emails.db", "src/data/emails.db-shm", "src/data/emails.db-wal", "src/data/.secret.key", "src/data/.session.key") | ForEach-Object {
            Remove-Item -Force -ErrorAction SilentlyContinue $_
        }

        # Full Docker teardown with Tailscale compose files
        $downArgs = @("compose") + $COMPOSE_FILES_TS + @("down", "--rmi", "all", "--volumes", "--remove-orphans")
        & docker @downArgs

        # Remove Tailscale state directory
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue "tailscale-state"
    }

    default {
        Write-Host "Unknown task: $Task" -ForegroundColor Red
        Write-Host "Run './commands.ps1' to see available tasks."
        exit 1
    }
}
