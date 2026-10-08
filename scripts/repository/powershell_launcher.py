"""PowerShell 5.1のargv/文字コード境界だけを扱う。option/defaultは対象Pythonが所有する。"""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import re
import runpy
import sys

ROOT = Path(__file__).resolve().parents[2]


def normalize_legacy_arguments(parser: argparse.ArgumentParser, arguments: list[str]) -> list[str]:
    """Python ownerの登録optionからPSのcase/prefix/colon/switch bindingを復元する。"""
    options = {name.casefold(): (name, action)
               for name, action in parser._option_string_actions.items()
               if name.startswith("-") and not name.startswith("--")}
    normalized = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        index += 1
        name, colon, value = argument.partition(":")
        if not name.startswith("-") or name.startswith("--"):
            normalized.append(argument)
            continue
        match = options.get(name.casefold())
        if match is None:
            candidates = [item for key, item in options.items() if key.startswith(name.casefold())]
            if len(candidates) == 1:
                match = candidates[0]
        if match is None:
            normalized.append(argument)
            continue
        canonical, action = match
        if colon and not value and index < len(arguments):
            value = arguments[index]
            index += 1
        if colon and action.nargs == 0:
            truth = value.casefold().removeprefix("$")
            if truth == "false":
                continue
            if truth != "true":
                parser.error(f"{canonical} requires a PowerShell boolean")
            normalized.append(canonical)
        elif action.nargs is None:
            # PS binderはquotedなleading-dash値もoperandとして受け取る。
            if not colon:
                if index >= len(arguments):
                    parser.error(f"{canonical} requires a value")
                value = arguments[index]
                index += 1
            normalized.append(canonical + "=" + value)
        elif action.nargs == "+":
            values = [value] if colon else []
            while index < len(arguments):
                operand = arguments[index]
                if operand.startswith("-") and not re.fullmatch(r"-[0-9]+(?:,[+-]?[0-9]+)*", operand):
                    break
                values.append(operand)
                index += 1
            if not values:
                parser.error(f"{canonical} requires a value")
            # PSの数値配列を一つのoperandにし、負数comma列をoptionとして解釈させない。
            normalized.append(canonical + "=" + ",".join(values))
        else:
            normalized.append(canonical)
            if colon:
                normalized.append(value)
    return normalized


def main(arguments: list[str] | None = None) -> int:
    script, encoded = sys.argv[1:] if arguments is None else arguments
    target = (ROOT / script).resolve()
    target.relative_to(ROOT)
    forwarded = json.loads(base64.b64decode(encoded, validate=True).decode("utf-8"))
    if not isinstance(forwarded, list) or not all(isinstance(item, str) for item in forwarded):
        raise ValueError("PowerShell argv must be a list of strings")
    previous = sys.argv
    try:
        sys.argv = [str(target), *forwarded]
        namespace = runpy.run_path(str(target), run_name="xpotato_powershell_entry")
        parser = namespace["build_parser"]()
        return namespace["main"](normalize_legacy_arguments(parser, forwarded))
    finally:
        sys.argv = previous


if __name__ == "__main__":
    raise SystemExit(main())
