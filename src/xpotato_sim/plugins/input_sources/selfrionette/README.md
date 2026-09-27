# selfrionette Input Source

## 意味とresponsibility

Selfrionette 7-channel loadcell frameを取得・parse・device-intrinsic normalizeしてcanonical sampleへ渡す。
canonical declaration: [`INPUT_SOURCE_PLUGIN`](plugin.py)

## input / output

injected / recorded lineまたは明示serial backendからframeを読み、loadcell vector sampleとhealthを出力する。
channelからendpointへの意味付けはMappingが所有する。

## parameters

port、baud rate、injected linesをrequest contractで扱う。current contractと選択規則は
[`plugin.py`](plugin.py)を正とする。

## lifecycleとside effect

declaration import時やport未指定時にserial portをopenしない。injected linesはsoftware-onlyである。
明示portを使うlive backendだけがstartでopenし、stopでcloseする。open / read failureはhealth / failure stateへ
反映し、暗黙fallbackしない。
start直後はinactiveで、成功vector受信後にactiveとなる。有限timeout・行数・byte・診断保持上限を持ち、
取得不能時は過去frameを返さず例外を伝播する。失敗したsessionはclose -> explicit startまで再使用しない。
時刻試験は`InputSourceRuntimeDependencies.clock`で注入する。既定injected clockは静的であり、実時間freshnessを表さない。

## hardware / transport boundary

live serialはoperator gateが必要である。device / port、physical clearance、stop手順を確認せず実行しない。
recorded / injected validationをhardware validationと呼ばない。

## compatibilityとcomposition

normalizationまでがInput Source責務であり、Mapping parameterやRobot selectionを所有しない。

## constraintsとnon-goals

- constraint: 7-channel protocolとfinite値をstrictに検証する
- non-goal: endpoint axis allocation、Robot command、hardware safety decisionを所有しない

## tests / validation

- [offline serial smoke](../../../../../docs/operations/r7-a-lite-serial-dry-run-smoke.md)
- [live operator procedure](../../../../../docs/operations/r7-b-manual-live-selfrionette-runtime-runner.md)

## canonical architecture / contract

- [serial frame contract](../../../../../docs/contracts/r7-a-lite-serial-frame-contract.md)
- [hardware safety](../../../../../docs/operations/hardware-safety.md)
