"""明示Device CLIへのargv/OS転送のみ。serial・option・defaultのownerはDevice。"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
COMMANDS = {"legacy-monitor", "legacy-measure"}


def main(arguments: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    if not arguments or arguments[0] not in COMMANDS:
        print("Deviceのlegacy-monitor / legacy-measure入口が必要です", file=sys.stderr)
        return 2
    command, *forwarded = arguments
    if forwarded[:1] == ["--powershell-json"]:
        try:
            if len(forwarded) != 2:
                raise ValueError("encoded argv is required")
            decoded = json.loads(base64.b64decode(forwarded[1], validate=True).decode("utf-8"))
            if not isinstance(decoded, list) or not all(isinstance(item, str) for item in decoded):
                raise ValueError("argv must be strings")
            forwarded = ["--powershell-args", *decoded]
        except (ValueError, UnicodeError) as error:
            print(f"PowerShell argvを復元できません: {error}", file=sys.stderr)
            return 2
    executable = Path(os.environ.get("SELFRIONETTECTL", ""))
    if not executable.is_absolute() or not executable.is_file() or executable.suffix.lower() in {".bat", ".cmd", ".ps1"}:
        print("採用済みDeviceのnative CLI絶対pathをSELFRIONETTECTLへ明示してください。"
              "自動build/downloadや旧実装へのfallbackは行いません。", file=sys.stderr)
        return 1
    try:
        # 旧binaryに能力がない場合はserial open以前に止める。optionの解釈はしない。
        probe = subprocess.run([str(executable), command, "--help"], cwd=ROOT, shell=False,
                               capture_output=True, text=True, encoding="utf-8")
        if probe.returncode:
            print(f"Device CLIが{command}に対応していません: {probe.stderr.strip()}", file=sys.stderr)
            return 1
        return subprocess.call([str(executable), command, *forwarded], cwd=ROOT, shell=False)
    except OSError as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
