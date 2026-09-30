"""有限trialの排他的ローカル記録。terminalだけを完了markerとする。"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path

from xpotato_sim.runtime.scene.objects import canonical


@dataclass(frozen=True, slots=True)
class TrialResult:
    """後続のretryに書き換えられない最小結果。完全metric artifactではない。"""
    document: bytes

    def to_document(self):
        return json.loads(self.document)


class TrialRecorder:
    """directoryと全fileを新規作成し、既存結果を上書きしない。"""
    def __init__(self, root: Path, trial_id: str):
        self.directory = Path(root).resolve() / trial_id
        self.directory.parent.mkdir(parents=True, exist_ok=True)
        self.directory.mkdir(exist_ok=False)
        self._terminal = False

    def write(self, name: str, document: bytes):
        """検証済みstagingをhard linkで排他的公開し、未検証の完了markerを残さない。"""
        path = self.directory / name
        pending = self.directory / (name + ".pending")
        with pending.open("xb") as stream:
            stream.write(document)
            stream.flush()
            os.fsync(stream.fileno())
        if pending.read_bytes() != document:
            raise OSError("trial record read-back mismatch")
        # linkは既存targetを上書きしない。検証後・公開後は内容を変更しない。
        os.link(pending, path)
        try:
            pending.unlink()
        except OSError:
            # 公開済み結果の成否をcleanup失敗で反転させない。同じ不変byteの残存link。
            pass
        return {"file": name, "sha256": sha256(document).hexdigest()}

    def start(self, *, condition, provenance, initial_state, record):
        condition_ref = self.write("condition.json", canonical({
            "condition": condition.to_document(), "condition_sha256": condition.digest,
            "origin_profile": provenance}))
        initial_ref = self.write("initial-state.json", canonical(initial_state))
        return self.write("start.json", canonical({**record,
            "condition_ref": condition_ref, "initial_state_ref": initial_ref}))

    def terminal(self, *, final_state, record):
        if self._terminal:
            raise RuntimeError("terminal already committed")
        final_ref = self.write("final-state.json", canonical(final_state))
        result = TrialResult(canonical({**record, "final_state_ref": final_ref, "recording": "complete"}))
        self.write("terminal.json", result.document)
        self._terminal = True
        return result
