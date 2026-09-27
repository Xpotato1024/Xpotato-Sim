---
status: supporting
owner: architecture
last_verified: 2026-07-30
canonical_for: []
related:
  - docs/architecture/code-documentation-policy.md
---

# code documentation review checklist

このchecklistは
[`code-documentation-policy.md`](../architecture/code-documentation-policy.md)をreviewへ適用するための
supporting文書であり、規則本文の正本ではない。判断に迷う場合はcanonical policyを優先する。

## accuracyとrationale

- [ ] comment、docstring / JSDoc、READMEがactual codeと一致し、staleな説明を削除した。
  ([正本](../architecture/code-documentation-policy.md#目的と適用範囲))
- [ ] commentはWhat / Howの実況ではなく、必要なWhy、constraint、invariantを説明している。
  ([正本](../architecture/code-documentation-policy.md#commentの原則))
- [ ] behavior、ownership、contract変更に関連するdocumentationを同じPRで同期した。
  ([正本](../architecture/code-documentation-policy.md#maintenanceとenforcement))

## contract coverage

- [ ] contract significanceのあるpublic / exported surfaceを説明し、keywordやsymbol数だけで判定していない。
  ([正本](../architecture/code-documentation-policy.md#python-docstringとtypescript-jsdocの境界))
- [ ] 該当するunit、coordinate frame、orderingを誤読できない。
  ([正本](../architecture/code-documentation-policy.md#該当時に説明するsemantic))
- [ ] 該当するlifecycle、state transition、side effect、thread / async assumptionを説明している。
  ([正本](../architecture/code-documentation-policy.md#該当時に説明するsemantic))
- [ ] validation、failure、rejection、hold behaviorとmaterial non-goalを必要な範囲で説明している。
  ([正本](../architecture/code-documentation-policy.md#該当時に説明するsemantic))

## debtとcompatibility

- [ ] TODO / FIXMEはowner、未完了内容、成立または削除条件、scope / riskを追跡できる。
  ([正本](../architecture/code-documentation-policy.md#todo--fixme))
- [ ] commented-out dead codeや旧実装のcopy-paste backupを残していない。
  ([正本](../architecture/code-documentation-policy.md#commented-out-code))
- [ ] suppressionはrule / scopeを限定し、必要なrationaleが近接している。
  ([正本](../architecture/code-documentation-policy.md#suppression))
- [ ] compatibility / workaroundはcurrent owner、維持理由、consumer、retirement conditionを必要に応じて示す。
  ([正本](../architecture/code-documentation-policy.md#compatibilityとworkaround))

## READMEと言語

- [ ] READMEはplugin root / axis / concrete pluginのlocal responsibilityを守り、second SoTを作っていない。
  ([正本](../architecture/code-documentation-policy.md#readmeの責務))
- [ ] `src/xpotato_sim/plugins/`をREADME rootとし、axis固有semanticを該当しないpluginへ要求していない。
  ([正本](../architecture/code-documentation-policy.md#readmeの責務))
- [ ] 日本語本文とcanonical Englishのidentifier / literal境界を守り、errorやcontractを変更していない。
  ([正本](../architecture/code-documentation-policy.md#言語境界))
- [ ] detailed architecture / contractはcanonical ownerへroutingし、長文を複製していない。
  ([正本](../architecture/code-documentation-policy.md#readmeの責務))

## scopeとvalidation

- [ ] documentationだけの変更がproduction behavior、public API、schema、plugin identityを変えていない。
- [ ] completionをcomment数、docstring数、README数、文字数、日本語文字率だけで判定していない。
  ([正本](../architecture/code-documentation-policy.md#maintenanceとenforcement))
- [ ] 変更したMarkdownのUTF-8、BOM、mojibake、relative link、final newlineを検証した。
  ([encoding正本](japanese-doc-writing-guardrails.md))
