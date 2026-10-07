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

# 新しい操作はPython script recipeでargvを保持し、shellへ引数を埋め込まない。
set script-interpreter := ["uv", "run", "--no-project", "--no-config", "--no-cache", "--no-env-file", "--offline", "--no-python-downloads", "--python", "3.12", "python"]

[script]
[positional-arguments]
app *args:
    import runpy, sys
    main = runpy.run_path("scripts/repository/commands.py")["main"]
    sys.exit(main("app", sys.argv[1:]))

[script]
[positional-arguments]
profile *args:
    import runpy, sys
    main = runpy.run_path("scripts/repository/commands.py")["main"]
    sys.exit(main("profile", sys.argv[1:]))

[script]
[positional-arguments]
replay *args:
    import runpy, sys
    main = runpy.run_path("scripts/repository/commands.py")["main"]
    sys.exit(main("replay", sys.argv[1:]))

[script]
[positional-arguments]
viewer-publisher *args:
    import runpy, sys
    main = runpy.run_path("scripts/repository/commands.py")["main"]
    sys.exit(main("viewer-publisher", sys.argv[1:]))

[script]
[positional-arguments]
trial *args:
    import runpy, sys
    main = runpy.run_path("scripts/repository/commands.py")["main"]
    sys.exit(main("trial", sys.argv[1:]))

[script]
[positional-arguments]
test *args:
    import runpy, sys
    main = runpy.run_path("scripts/repository/commands.py")["main"]
    sys.exit(main("test", sys.argv[1:]))

[script]
[positional-arguments]
lint *args:
    import runpy, sys
    main = runpy.run_path("scripts/repository/commands.py")["main"]
    sys.exit(main("lint", sys.argv[1:]))

[script]
[positional-arguments]
typecheck *args:
    import runpy, sys
    main = runpy.run_path("scripts/repository/commands.py")["main"]
    sys.exit(main("typecheck", sys.argv[1:]))

[script]
[positional-arguments]
launcher-typecheck *args:
    import runpy, sys
    main = runpy.run_path("scripts/repository/commands.py")["main"]
    sys.exit(main("launcher-typecheck", sys.argv[1:]))

[script]
[positional-arguments]
compile *args:
    import runpy, sys
    main = runpy.run_path("scripts/repository/commands.py")["main"]
    sys.exit(main("compile", sys.argv[1:]))

[script]
[positional-arguments]
docs-check *args:
    import runpy, sys
    main = runpy.run_path("scripts/repository/commands.py")["main"]
    sys.exit(main("docs-check", sys.argv[1:]))

[script]
[positional-arguments]
github-body-check *args:
    import runpy, sys
    main = runpy.run_path("scripts/repository/commands.py")["main"]
    sys.exit(main("github-body-check", sys.argv[1:]))

[script]
[positional-arguments]
viewer-test *args:
    import runpy, sys
    main = runpy.run_path("scripts/repository/commands.py")["main"]
    sys.exit(main("viewer-test", sys.argv[1:]))

[script]
[positional-arguments]
viewer-typecheck *args:
    import runpy, sys
    main = runpy.run_path("scripts/repository/commands.py")["main"]
    sys.exit(main("viewer-typecheck", sys.argv[1:]))

[script]
[positional-arguments]
viewer-build *args:
    import runpy, sys
    main = runpy.run_path("scripts/repository/commands.py")["main"]
    sys.exit(main("viewer-build", sys.argv[1:]))

[script]
[positional-arguments]
viewer-smoke *args:
    import runpy, sys
    main = runpy.run_path("scripts/repository/commands.py")["main"]
    sys.exit(main("viewer-smoke", sys.argv[1:]))

[script]
[positional-arguments]
selfrionette-dry-run *args:
    import runpy, sys
    main = runpy.run_path("scripts/repository/commands.py")["main"]
    sys.exit(main("selfrionette-dry-run", sys.argv[1:]))

[script]
[positional-arguments]
fast-arm-motion-sanity *args:
    import runpy, sys
    main = runpy.run_path("scripts/repository/commands.py")["main"]
    sys.exit(main("fast-arm-motion-sanity", sys.argv[1:]))

check: lint typecheck launcher-typecheck compile

check-all: check test viewer-test viewer-typecheck viewer-build

alias test-python := test
