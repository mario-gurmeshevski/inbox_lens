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
    Write-Host "  test-cov         Run tests with coverage"
    Write-Host "  lint             Run linters (Ruff for Python, djlint for templates)"
    Write-Host "  format           Format Python (Ruff) and templates (djlint)"
    Write-Host "  clean            Remove caches and compiled files"
    Write-Host "  reset            Clean + remove DB and secret key"
    Write-Host "  up               Start Docker Compose (auto-detect host IP)"
    Write-Host "  up-ts            Start Docker Compose with Tailscale"
    Write-Host "  down             Stop and remove Docker Compose"
    Write-Host "  stop             Stop Docker Compose containers (no data removed)"
    Write-Host "  start            Start previously-stopped Docker Compose containers"
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
$COMPOSE_FILES_TS = @("-f", "docker-compose.yaml", "-f", "docker-compose.tailscale.yaml")

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

function Invoke-Clean {
    Get-ChildItem -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Get-ChildItem -Recurse -Directory -Filter '.pytest_cache' | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Get-ChildItem -Recurse -Filter '*.pyc' | Remove-Item -Force -ErrorAction SilentlyContinue
}

switch ($Task) {
    "install" {
        if (-not (Test-Path $VENV)) { & $PYTHON -m venv $VENV }
        & $VENV_PIP install -e .
    }
    "uninstall" {
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $VENV
    }
    "dev-install" {
        if (-not (Test-Path $VENV)) { & $PYTHON -m venv $VENV }
        & $VENV_PIP install -e ".[dev]"
    }
    "web" { & $VENV_PYTHON -m uvicorn src.scripts.web:app --host $WEB_HOST --port $WEB_PORT --reload }
    "test" { & $VENV_PYTHON -m pytest src/tests/ -v }
    "test-cov" { & $VENV_PYTHON -m pytest src/tests/ --cov=src/scripts --cov-report=term-missing }
    "lint" {
        & $VENV_PYTHON -m ruff check src/
        & $DJLINT --check src/web/templates
    }
    "format" {
        & $VENV_PYTHON -m ruff format src/
        & $DJLINT --reformat src/web/templates
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
    }

    "up-ts" {
        $argList = @("compose") + $COMPOSE_FILES_TS + @("up", "--build")
        if ($ExtraArgs) { $argList += $ExtraArgs }
        & docker @argList
    }

    "down" {
        docker compose down --rmi local --volumes --remove-orphans
    }

    "stop" {
        $argList = @("compose") + $COMPOSE_FILES_TS + @("stop")
        & docker @argList
    }

    "start" {
        $argList = @("compose") + $COMPOSE_FILES_TS + @("start")
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
        $logoutArgs = @("compose") + $COMPOSE_FILES_TS + @("exec", "tailscale", "/usr/local/bin/tailscale", "logout")
        & docker @logoutArgs 2>$null

        Invoke-Clean
        @("src/data/emails.db", "src/data/emails.db-shm", "src/data/emails.db-wal", "src/data/.secret.key", "src/data/.session.key") | ForEach-Object {
            Remove-Item -Force -ErrorAction SilentlyContinue $_
        }

        $downArgs = @("compose") + $COMPOSE_FILES_TS + @("down", "--rmi", "all", "--volumes", "--remove-orphans")
        & docker @downArgs
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue "tailscale-state"
    }

    default {
        Write-Host "Unknown task: $Task" -ForegroundColor Red
        Write-Host "Run './commands.ps1' to see available tasks."
        exit 1
    }
}
