"""Convert a saved Workbench condition to a role-qualified preset ID, without overwriting it."""

from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path

from xpotato_sim.runtime.experiment.edited_condition import MAX_BYTES, resolve_condition
from xpotato_sim.runtime.scene.objects import canonical, strict_json


OLD_PRESETS = {
    "research-spring": "tasks/research-spring",
    "shared-dynamic-cube-drop": "tasks/dynamic-cube-drop",
    "shared-dynamic-cube-push": "tasks/dynamic-cube-push",
    "shared-dynamic-fixed-contact": "tasks/dynamic-fixed-contact",
    "research-spring-g": "tasks/research-spring",
    "research-spring-s": "tasks/research-spring",
    "dynamic-cube-drop": "tasks/dynamic-cube-drop",
    "dynamic-cube-push": "tasks/dynamic-cube-push",
    "dynamic-fixed-contact": "tasks/dynamic-fixed-contact",
    "dynamic-fixed-contact-bimanual-device": "diagnostics/fixed-contact-bimanual-selfrionette-fixture",
    "dynamic-fixed-contact-single-device": "diagnostics/fixed-contact-single-selfrionette-fixture",
    **{f"contact-debug-{part}": f"diagnostics/contact-debug-{part}"
       for part in ("bimanual", "left", "right", "single")},
    "fast-arm-bimanual-gamepad": "inputs/bimanual-gamepad",
    "fast-arm-bimanual-device": "inputs/bimanual-selfrionette-fixture",
    "fast-arm-single-gamepad": "inputs/single-gamepad",
    "fast-arm-single-device": "inputs/single-selfrionette-fixture",
    "fast-arm-left-gamepad": "examples/left-gamepad",
    "fast-arm-right-gamepad": "examples/right-gamepad",
}


def convert_document(content: bytes) -> bytes:
    if len(content) > MAX_BYTES:
        raise ValueError("条件JSONが上限を超えています")
    document = strict_json(content)
    if type(document) is not dict:
        raise ValueError("条件JSON objectが必要です")
    old_id = document.get("preset_id")
    if old_id not in OLD_PRESETS:
        raise ValueError(f"変換対象外の旧preset ID: {old_id!r}。当時のcommitで確認してください")
    updated = deepcopy(document)
    updated["preset_id"] = OLD_PRESETS[old_id]
    try:
        _, _, normalized = resolve_condition(updated)
    except (TypeError, ValueError) as exc:
        raise ValueError("新しいtask/input定義へ自動変換できません。元ファイルを保持し、"
            "当時のcommitと条件・依存固定情報で再現するか、条件差分を確認して手動移行してください") from exc
    return canonical(normalized)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="保存済みの旧条件JSON（読み取りのみ）")
    parser.add_argument("output", type=Path, help="未作成の新しい出力JSON")
    args = parser.parse_args()
    if args.source.resolve() == args.output.resolve():
        parser.error("元ファイルと出力先は別にしてください")
    converted = convert_document(args.source.read_bytes())
    with args.output.open("xb") as destination:
        destination.write(converted)
    if args.output.read_bytes() != converted:
        raise OSError("変換後の読み戻しに失敗しました")
    print(f"変換済み条件を保存: {args.output}")
    print("実機のidentity・校正・role接続は、この変換で検証または補完されません")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
