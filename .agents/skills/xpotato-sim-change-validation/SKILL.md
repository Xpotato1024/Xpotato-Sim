---
name: xpotato-sim-change-validation
description: Xpotato-Simの変更層、contract、failure mode、side effectに応じて必要なvalidationを選び、実行結果と未実行理由を報告する。実装、bug fix、docs・plugin変更の完了前に使用し、full suiteの機械適用やhardware・serial・OSC・production操作には使用しない。
---

# Xpotato-Sim change validation

## Triggerとexclusion

変更の責務層とfailure modeが明確で、focused test、architecture test、compile、build、
MuJoCo、viewer、docs、Git auditから必要な検証を選ぶときに使用する。全taskへfull suiteを
機械的に適用しない。test削除、skip、弱体化、未実行項目の成功扱い、simulation smokeの
hardware validationへの読み替えはしない。hardware、serial、OSC、production操作は対象外である。

## Required inputsと取得方法

- `AGENTS.md`と`docs/operations/validation.md`を最初に読む。
- actual diff、変更層、contract、consumer、failure mode、side effectをGitと実装から取得する。
- docs、Git / PR、hardware safetyの関連canonical documentを必要範囲で読む。
- tests、build設定、既存validatorから利用可能なfocused commandを確認する。

変更のSoTや実行環境が取得できない場合は、実行せずNot Run Reason、代替証拠、残存riskを出力する。

## Ordered workflow

1. task scope、base、actual diff、working treeを確認し、unrelated changeを分離する。
2. 変更層とcontract ownerを特定し、想定failure modeとside effectを列挙する。
3. 既存の検証記録と現在の関連条件を照合し、有効な結果を再利用する。不足・失効した範囲だけに最小のfocused regression / unit / architecture testを選ぶ。
4. compile / typecheck / build、MuJoCo model load / forward / step、replay / dry-run、viewer smokeを、該当する層だけ追加する。
5. docs link / frontmatter / encoding / mojibake、Git diff / PR metadata auditを必要範囲で行う。
6. 実行結果をcategory別に記録し、未実行は理由と代替証拠を明示する。
7. contract、scope、hardware境界、Documentation / Research / Experiment impactを完了前に確認する。

## Permitted variation

test数とsmokeの選択は変更層、failure mode、side effect、既存canonical docsに応じて変えてよい。
環境制約でcommandを実行できない場合は、project environmentを勝手に再構成せず、利用可能な代替検証だけを実行する。

## OutputsとDefinition of Done

出力は、validation category、command、pass / fail / not run、理由、代替証拠、残存risk、
impact判定を含む。成功の主張は実測結果に限定する。

完了条件は、選択根拠が変更層とfailure modeに対応し、focused checksが実行または有効な既存証拠で充足され、docsとGitの
scope gateが確認され、未実行項目が成功扱いされず、simulation smokeがhardware validationと混同されないことである。

## Failure、retry、stop / escalation

focused testまたはvalidatorが失敗したら原因を分類し、testを弱めずに停止または修正後の再実行へ進む。
環境修復後は小さいpreflightから失敗stepと影響範囲だけを再実行する。修正・新証拠のない同じretryや全件やり直しを行わない。再利用・復旧・統合E2Eの条件は`docs/operations/codex-workflow.md`を正とする。contract矛盾、scope外変更、dependency・CI拡張、
hardware gateの必要性が見つかったら停止してユーザーへescalateする。

## Side-effect boundary

通常はread-only validationである。許可された実装taskの範囲でtest、compile、build、dry-runを実行してよいが、
serial open、Arduino upload、OSC send、robot output、deployment、credentials、GitHub mutationは行わない。
