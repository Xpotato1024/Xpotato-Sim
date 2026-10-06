set minimum-version := "1.58.0"
set dotenv-load := false
[unix]
set shell := ["sh", "-eu", "-c"]
[windows]
set shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command"]

# Windows PowerShellも正式CLIの終了code（Ctrl+Cの130を含む）を保持する。
exit_status := if os_family() == "windows" { "; exit $LASTEXITCODE" } else { "" }

init:
    @uv run --no-project --no-config --no-cache --no-env-file --offline --no-python-downloads --python 3.12 python scripts/workbench_local.py init{{exit_status}}

setup:
    @uv run --no-project --no-config --no-cache --no-env-file --offline --no-python-downloads --python 3.12 python scripts/workbench_local.py setup{{exit_status}}

build:
    @uv run --no-project --no-config --no-cache --no-env-file --offline --no-python-downloads --python 3.12 python scripts/workbench_local.py build{{exit_status}}

run:
    @uv run --no-project --no-config --no-cache --no-env-file --offline --no-python-downloads --python 3.12 python scripts/workbench_local.py run{{exit_status}}

dev:
    @uv run --no-project --no-config --no-cache --no-env-file --offline --no-python-downloads --python 3.12 python scripts/workbench_local.py dev{{exit_status}}

doctor:
    @uv run --no-project --no-config --no-cache --no-env-file --offline --no-python-downloads --python 3.12 python scripts/workbench_local.py doctor{{exit_status}}
