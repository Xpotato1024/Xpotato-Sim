---
status: historical
owner: architecture
last_verified: 2026-10-07
canonical_for: []
related:
  - docs/operations/repository-commands.md
  - docs/operations/browser-visual-smoke.md
  - docs/operations/r7-a-lite-serial-dry-run-smoke.md
---

# ADR 0015: offline操作のPython ownerとPowerShell互換入口

## 背景と決定

#456の残件には記録済みloadcell plot、browser smoke、live serial monitor/measureがある。
研究判断や実機なしで検証できる前2つをPR620の操作転送へ積み、別Draftの独立差分とする。
後2つの校正・interactive測定はDeviceのidentity検証付きbounded monitorと能力が同じではないため、
置換や削除をこの差分へ混ぜない。

plotのparser/CSV/chartとbrowser smokeのoption/default/profile作成をPythonへ移す。
PowerShellはPS5.1のargv/stdin/文字コード/終了codeを扱う薄い入口として残し、
共通OS adapterがUTF-8 JSON/base64から空文字・引用符・日本語・arrayを復元する。
justは新Pythonへ直接argvを渡す。依存追加はなく、plotは既存matplotlibのAggを使う。
browserは正式appのloopback検査、startup-check、process所有・回収へ委譲し、
profile一時fileを例外/取消時にも回収する。

## 機能等価性と境界

| consumer | 維持する契約 | 検証 |
| --- | --- | --- |
| file/stdin/Clipboard plot | Clipboard→InputPath→stdin、全7ch CSV、sample順、signed64 timestamp、欠損/NaN、既定path、channel選択 | fixtureと実CSV/1600×900 PNG、旧.NET日本語cultureの非有限/overflow判定 |
| 旧PS plot | 同じoption名、呼出しcwdの相対path、pipeline入力、終了code | 実PS5.1 transportとoffline出力 |
| replay browser smoke | v1 profileの全既定値、NoBrowser優先、明示open、有限startup/cleanup | profile同値、正式resolver、status/原例外/temp回収 |
| 旧PS browser smoke | 同じoption名とapp委譲、空/引用符/Unicode | 実PS5.1 transport |
| just | literal argv/native終了code、準備済み環境 | 追加recipeと既存dispatcher回帰 |

PNGの描画engineを移すのでpixel完全一致ではなく、sample index、7 channel値、色、選択、寸法を拘束する。
旧Windows/.NET日本語cultureのInfinity表記は∞であり、文字列Infinityと数値overflowをNaNとして保持する。
CSVのBOMは既存Export-Csvのartifact互換性であり、文書/PythonはBOMなしとする。

physics、Mapping、Task、実験評価、寸法、研究条件・人数を変えない操作実装なので
research log/experiment note更新は不要と判定する。serial、OSC、browser実操作、実験端末の反復検証は行わない。
#456全体、#607全面退役、#617/#618の完了はこの変更から宣言しない。

## 独立レビューによる互換性の補完

独立レビューで旧Get-Contentが識別するUTF-16 BOMログと、旧PS binderのcase-insensitive option /
switch:$falseが新入口で拒否されることを再現した。
fileはUTF-8/UTF-16/UTF-32のBOMを識別し、WindowsのBOMなし既定encodingも維持する。
OS adapterは対象Pythonの登録parserからcase/prefix/colon/switch値を正規化し、
option名やdefaultの別定義を増やさない。BOM各種と旧bindingを回帰へ追加する。

最新mainへの統合レビューで、負数を含むchannel配列がoptionとして拒否されることと、
日本語titleがAggの既定fontで欠落することを再現した。数値配列は次のoption手前までを
一つのoperandとして復元し、空配列も保持する。titleは既存の日本語fontを選び、依存を増やさない。
負数・複数operand・colon・後続option・空配列と、実PS入口のCSV/PNG・日本語glyphを回帰で拘束する。
