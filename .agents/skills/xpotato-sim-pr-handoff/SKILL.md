---
name: xpotato-sim-pr-handoff
description: Selfrionette-mujocoのPR handoffをread-onlyで監査し、branch、base、actual diff、Issue scope、validation、local・remote・PR head、CI、body、impact、残存riskを突合する。Draft PR作成前後に使用し、commit、push、PR更新、merge等のmutationには使用しない。
---

# Xpotato-Sim PR handoff

## Triggerとexclusion

Draft PRの作成前後、handoff、completion report、review前にGitとGitHubの状態を突合するときに使用する。
既定ではread-onlyであり、commit、push、PR更新、merge、Ready化、Issue close、branch削除は行わない。
`mergeable: true`だけでready判定しない。Unicode-safe long-form bodyの更新は既存canonical ruleへrouteする。

## Required inputsと取得方法

- local branch、base、working tree、actual diff、local HEADをGitから取得する。
- remote branch HEAD、PR head、Draft state、CI、Issue relationをGitHubから取得する。
- task scope、validation結果、PR body、Documentation / Research / Experiment impact、残存riskを読む。
- 長文bodyを扱う場合は`docs/operations/git-pr-workflow.md`と
  `docs/operations/japanese-doc-writing-guardrails.md`を先に読み、full bodyを取得する。

取得できないremote stateやCIは成功扱いせず、Not Run / unavailableとして報告する。

## Ordered workflow

1. repository、branch、base、working tree、pre-existing changeを確認する。
2. baseとlocal HEADの比較diffを取得し、Issue scopeと変更fileを突合する。
3. local HEAD、remote branch HEAD、PR headの一致を確認する。
4. validation command、実測結果、未実行理由、CI conclusionを確認する。
5. PR title / body、Issue relation、Draft state、mergeability、approval / review stateを監査する。
6. Documentation impact、Research log impact、Experiment evidence impact、hardware / external side effect、残存riskを確認する。
7. handoff結果をread-only reportとしてまとめ、blockerがあればready相当と報告しない。

## Permitted variation

既存PRがない場合はPR headとCIをunavailableとして扱い、branch・base・diff・validationのhandoffだけを実施する。
bodyの更新が別途明示された場合も、full-body backup、write前再取得、exact read-back、structural preservationをcanonical ruleへ委譲する。

## OutputsとDefinition of Done

出力は、branch、base、actual diff、Issue scope、validation、local / remote / PR head、CI、Draft / review state、
body、3つのimpact、remaining risks、未確認項目を含む。ready判定を出す場合はmergeability以外の各gateも明示する。

完了条件は、read-onlyで取得したstateが一致し、actual diffにunrelated fileがなく、validationのprovenanceが明確で、
Draft状態・Issue relation・未実行項目・残存riskを誤って隠していないことである。

## Failure、retry、stop / escalation

head不一致、dirty tree、base不明、Issue scope外diff、CI失敗、bodyの欠落・文字化け、並行更新を検出したら停止する。
read-onlyでcurrent stateを一度再取得しても解消しない場合はユーザーへescalateし、write操作で修復しない。

## Side-effect boundary

GitとGitHubのread-only inspectionだけを許可する。commit、stage、push、PR create / update、merge、Ready化、
Issue close、branch削除、release、deploy、credentials、hardware、external mutationは行わない。
