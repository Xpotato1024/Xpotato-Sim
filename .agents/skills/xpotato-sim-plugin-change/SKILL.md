---
name: xpotato-sim-plugin-change
description: Selfrionette-mujocoのRobot、Environment、Mapping、Task、Evaluation、Input Sourceに対するplugin追加・material変更を、read-only監査または明示されたwrite taskの許可範囲内で支援する。plugin変更の実装またはreview前に使用し、無関係なruntime・viewer・simulation・hardware変更は扱わない。
---

# Xpotato-Sim plugin change

## Triggerとexclusion

6 axisのplugin追加、登録、発見、identity、runtime composition、ownership、resource、README、
contract変更を扱うときに使用する。read-only taskではcontract、ownership、discovery、identity、
registration、composition、docs、tests、impactの監査だけを行う。対象Issueと`AGENTS.md`が許可する
明示されたwrite taskでは、plugin implementation、registration / catalog、discovery、plugin-owned
resources、directly required tests、plugin-local README、axis README、related canonical docsを変更してよい。
単なる一般docs修正、単発fixture、既存behaviorの無関係なcleanup、pluginと無関係なstub、不要なcompat
layer、parallel implementationは対象外である。Skill自身は権限を与えず、current Issue、branch、SHA、
特定実装の一時状態をSkill本文へ固定しない。

## Required inputsと取得方法

- `AGENTS.md`、`docs/README.md`、`docs/architecture/dependency-boundaries.md`、
  `docs/architecture/runtime-composition.md`を読む。
- 対象axisのcontract、axis README、plugin-local README、catalog / registration / discoveryを取得する。
- actual diff、consumer、tests、関連canonical docs、research / experiment impact条件を確認する。
- pluginのlogical identity、version、resource、lifecycle、side effect、failure semanticsを実装から確認する。

不明なownership、duplicate identity、version mismatch、未登録path、またはcurrent SoT間の矛盾は推測で補完しない。

## Ordered workflow

1. read-only taskか、Issue scopeと`AGENTS.md`が明示的に許可するwrite taskかを確認する。modeが不明なら停止する。
2. 対象axisをRobot、Environment、Mapping、Task、Evaluation、Input Sourceのいずれかに限定する。
3. axis contract、owner、入力・出力、lifecycle、side effect、failure semanticsを照合する。
4. discovery、identity、registration / catalog、version compatibility、resource ownershipを確認する。
5. runtime composition rootへの接続、viewer safety、legacy import、parallel implementationの有無を監査する。
6. write taskでは許可されたplugin実装・catalog・resource・directly required tests・README・canonical docsだけを変更し、read-only taskでは変更しない。
7. Documentation impact、Research log impact、Experiment evidence impactを実質的影響で判定する。
8. `xpotato-sim-change-validation`へvalidationをrouteし、scope外の不足は停止条件として報告する。

## Permitted variation

axisごとにcatalog、resource、runner、README、lifecycleの具体的ownerは異なる。既存のcanonical
contract、実装、testsの組合せを選び、未実装のaxisへplanned behaviorをcurrent behaviorとして追加しない。
read-only taskの監査結果と、明示されたwrite taskのscoped changeは分けて報告する。

## OutputsとDefinition of Done

read-only taskの出力は、axis、contract、ownership、discovery、identity、registration、composition、
README、tests、canonical docs、3つのimpact判定、selected validation、残存riskを含む監査結果とする。
write taskの出力はこれに加え、変更したplugin実装・catalog・resource・tests・docsのscopeとvalidation evidenceを含む。

完了条件は、modeが明確で、plugin追加と無関係なstub・compat layer・parallel implementationがなく、
logical identityとcontractが一意で、runtime compositionとresource ownershipがSoTに一致し、必要なREADME・docs・testsの
要否を実測していることである。write taskでは許可範囲外の変更がなく、read-only taskではSkill関連ファイルを変更していないことも確認する。

## Failure、retry、stop / escalation

unknown、duplicate、version mismatch、missing capability、ambiguous owner、暗黙fallback、または
必要なpublic schema / contract変更が見つかったらfail closedで停止する。read-only再取得でcurrent stateを一度確認し、
矛盾が残る場合は実装を広げずユーザーへescalateする。Issue scope外のpublic contract、重大なschema変更、dependency追加、
runtime compositionのmaterial expansion、unrelated viewer / simulation、compatibility layer、parallel implementation、
hardware / serial / OSC、production / credentials、external mutationが必要なら、write taskでも承認なしに進めない。
modeまたは権限が不明な場合はretryを1回だけread-only再確認へ限定し、解消しなければ停止する。

## Side-effect boundary

`instruction-only`はSkill自身が手順を記述・検証するだけで、外部side effectを実行しないという意味である。
それは対象taskを一律read-onlyにする意味ではない。read-only taskではaudit onlyとし、write taskでは対象Issueと
`AGENTS.md`が許可する範囲に限りplugin implementation、registration / catalog、discovery、plugin-owned resources、
directly required tests、plugin-local README、axis README、related canonical docsを変更してよい。
いずれの場合もSkillは新しい権限を与えない。Issue scope外のpublic contract、重大なschema、dependency、runtime compositionの
material expansion、unrelated viewer / simulation、compatibility layer、parallel implementation、hardware、serial、OSC、
production、credentials、external mutationは停止・承認対象とする。
