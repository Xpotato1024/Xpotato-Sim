---
status: canonical
owner: architecture
last_verified: 2026-07-30
canonical_for:
  - code and plugin documentation policy
related:
  - docs/architecture/development-policy.md
  - docs/architecture/documentation-sot-policy.md
  - docs/operations/japanese-doc-writing-guardrails.md
  - docs/operations/plugin-readme-templates.md
  - docs/operations/code-documentation-review-checklist.md
---

# code・plugin documentation方針

## 目的と適用範囲

この文書は、implementation comment、Python docstring、TypeScript JSDoc、TODO / FIXME、
commented-out code、lint / type suppression、compatibility / workaround、directory README、
plugin root / axis / concrete plugin READMEの一意な正本である。

codeだけでは読み取れない契約、理由、制約を人間とagentへ伝える。documentationの量ではなく、
正確性、責務、保守性を評価する。staleな説明は説明の欠落より危険であるため、behavior、ownership、
contractを変更する場合は、関連するcomment、docstring / JSDoc、READMEを同じPRで更新または削除する。

この文書は文書lifecycleやfront matterを所有せず、
[`documentation-sot-policy.md`](documentation-sot-policy.md)へ委譲する。UTF-8、BOM、mojibake、
GitHub long-form transportは
[`japanese-doc-writing-guardrails.md`](../operations/japanese-doc-writing-guardrails.md)を正とする。
subsystem architectureやcontractそのものは[`docs/README.md`](../README.md)から各canonical documentを
参照する。

## commentの原則

codeは主にWhat / Howを表し、commentはcodeだけでは読み取れないWhy、constraint、invariantを説明する。
次に該当し、名前、型、function分割だけでは誤読を防げない場合にcommentを置く。

- ownership boundaryとfail-closedにする理由
- lifecycle、state continuity、state transition、side effect
- hardware access、transport、thread / async assumption
- coordinate frame、unit、ordering、mathematical / numerical convention
- compatibility rationale、workaround、dependent consumer、retirement condition
- 非自明なedge case、validation、failure / rejection / hold behavior
- lint / type suppressionのrationale

次は原則として書かない。

- codeの逐語訳、行ごとの実況、function名の言い換え
- obviousな代入、分岐、returnの説明
- READMEやcanonical documentの長文複製
- Git historyの代替となるIssue、PR、branch、日付、担当者の作業履歴
- staleな旧実装の説明

履歴上重要な判断はGit history、Issue、ADR、reportへ置き、current codeの近接commentには現在も成立する
理由と終了条件だけを残す。

## Python docstringとTypeScript JSDocの境界

### 必須対象

visibility、`export` keyword、命名だけで機械判定せず、contract significanceで判断する。次は、
callerが正しく利用するために名前と型以外の説明を必要とする場合、docstringまたはJSDocを必須とする。

- public package surfaceとpackage-root export
- layer間で使用されるpublic class / function
- Protocol、schema、contract boundary
- public dataclass / TypedDict
- plugin fixed entry pointとplugin declaration object
- lifecycleまたはside effectを持つpublic API
- unit、coordinate frame、orderingの誤読でbehaviorが変わるAPI
- non-obviousなvalidation、failure、rejection、hold semanticsを持つAPI

public / exported surfaceでも、re-export元のcontractへ明確にroutingできるthin facadeや、名前と型だけで
契約が完結する単純な値へ長文説明を機械的に要求しない。package-root exportは、local docstring、
module documentation、canonical contractへのroutingのいずれかで責務を追跡可能にする。

### 必要に応じる対象

次のprivate surfaceは誤読riskがある場合だけ説明する。

- algorithmically non-trivialなhelper
- lifecycle、state continuity、numerical conventionを持つhelper
- hardware / transport boundary
- compatibility wrapperまたはworkaround

### 原則不要な対象

- obviousなprivate helper
- thin getter、trivial property
- 名前と型で意味が明確なwrapper
- Protocol memberごとの冗長な繰り返し
- re-export元のcontractをそのまま複製するdocstring

全private helper、全property、全`__post_init__`、全moduleへ一律にdocstringを要求しない。

## 該当時に説明するsemantic

対象APIに実在するsemanticだけを選び、必要な粒度で説明する。

- responsibility、input / output、owner
- unit、coordinate frame、ordering
- lifecycle、state transition、side effect
- thread / async assumption
- validation、failure / rejection / hold behavior
- compatibility rationale、deprecation / retirement condition
- material non-goal

すべてのAPIへ同じsectionを機械的に追加しない。型、field名、canonical contractへのlinkで十分な内容を
複製せず、利用判断を変える情報を優先する。

## 言語境界

README、docstring、JSDoc、implementation commentは日本語を基本とする。次はcanonical Englishを
維持する。

- identifier、API / schema field、CLI option
- path / filename、unit、frame名
- protocol literal、external formal name、error literal、public contract literal

`Args:`、`Returns:`、`Raises:`、`Notes:`等のsection headingは既存styleに合わせて英語を維持してよい。
error messageやexternal contractをdocumentation目的だけで翻訳または変更しない。日本語文字率だけで
品質や適合性を判定せず、説明対象とcanonical literalの境界をreviewする。

## TODO / FIXME

committed TODO / FIXMEは、少なくとも次を追跡可能にする。

- Issue番号または同等に一意なowner
- 未完了内容
- 成立条件または削除条件
- scopeまたはrisk

推奨形式は次のとおりとする。

```text
TODO(#123): <未完了内容>。<成立条件または削除条件>
```

`TODO: later`、単独の`FIXME`、`temporary`、`cleanup someday`は残さない。同一PR内で解消できる
軽微な事項はTODO化せず解消する。tracking Issueがないmaterial workへ架空番号や曖昧なownerを付けず、
scope外ならfindingとして報告して追跡方法を決める。

## commented-out code

dead codeをcomment-outして保存しない。copy-paste backupや旧実装退避にはGit historyを使う。
切替可能性や再現条件がcurrent contractとして必要な場合は、commentではなく次の明示的な仕組みを使う。

- feature flag
- dedicated debug mode
- test fixture
- Issue
- ADR

## suppression

`# noqa`、`# type: ignore`、eslint disable、TypeScript suppression、formatter / linter disable、
warning suppressionには次を適用する。

- rule codeまたは対象範囲を可能な限り限定し、blanket suppressionを避ける。
- 理由が自明でない場合は近接commentでrationaleを記録する。
- compatibility re-export等、module-level policyで理由が一意な場合は各行へ同じ説明を複製しない。
- 不要になったsuppressionは削除する。
- suppressionを型または契約問題の隠蔽に使用しない。

suppressionの存在自体を一律に禁止せず、対象rule、現行理由、より狭い表現が可能かをreviewする。

## compatibilityとworkaround

compatibility wrapperまたはworkaroundには、該当する場合に次を記録する。

- current ownerと維持理由
- dependent consumer
- removal conditionまたはretirement gate
- fallbackを禁止するか

Issue番号や日付だけをcurrent rationaleにしない。終了条件を特定できない場合は、恒久contractとして
残すのか、trackingが必要なdebtなのかを明示する。

## READMEの責務

READMEはlocal entry pointとroutingを担い、catalog、declaration、schema、canonical architectureの
第二SoTを作らない。作成時は
[`plugin-readme-templates.md`](../operations/plugin-readme-templates.md)を使用できる。

plugin READMEのrepository rootからの基準pathは`src/xpotato_sim/plugins/`である。root直下に
別の`plugins/`を作らない。

### plugin root README

`src/xpotato_sim/plugins/README.md`は次を説明する。

- plugin system全体への入口とsix-axis composition
- logical identityとbounded discovery
- axis ownership
- canonical architecture / contractへのrouting
- current production axisとgeneric-only axisの区別

詳細contractや全plugin listを独立して維持するregistryにはしない。

### axis README

`src/xpotato_sim/plugins/<axis>/README.md`は次を説明する。

- axisの責務と、置けるもの / 置けないもの
- required contract、input / output
- lifecycle / side effect
- catalog / discovery / registration owner
- shared private owner
- concrete pluginの追加方法
- canonical docsへのrouting

### concrete production plugin README

`src/xpotato_sim/plugins/<axis>/<plugin>/README.md`は、6軸のどのconcrete production pluginにも
適用できる共通責務として次を説明する。対象はRobot、Input Source、Control Mapping、
Environment、Task、Evaluationである。

- pluginの意味とlogical identityへのcanonical link
- local responsibility
- input / output、またはcomposition上のrole
- parameters、またはparameterを持たないこと
- lifecycleとside effect
- compatibility / composition boundary
- constraintsとnon-goals
- tests / validation入口
- canonical architecture / contractへのrouting

command semantics routeは共通必須項目にしない。次のようにselected routeがplugin contractまたは
behaviorへ関与する場合だけ、該当するREADMEで説明する。

- Control Mappingがcommand semantics routeを宣言する場合
- Robot providerがtyped Robot command semanticを受理する場合
- native command passthroughを持つ場合
- selected routeがplugin behaviorを実質的に変える場合

Environment、Task、Evaluation等、Robot command routeを所有または消費しないpluginへ
command semanticsの記述を要求しない。

discoverable concrete production pluginにはREADMEを必須とする。ただし、READMEをplugin declarationや
catalogのsecond registryにせず、identityの値はcanonical declarationへlinkする。新規または変更する
discoverable pluginは、同じPRでREADMEを追加または更新する。既存pluginのcoverage debtを解消する
remediationは、policy変更とは独立したreview unitに分離できる。

### READMEを機械的に要求しない対象

- private helper file
- generated directory、local artifact directory
- small internal implementation directory
- test fixtureの個別directory
- parent READMEで責務が十分なprivate support package

directory数やREADME数ではなく、人間向け入口、責務境界、canonical routingの必要性で判断する。

## maintenanceとenforcement

behavior、ownership、contract、plugin identity、lifecycle、side effectを変更したPRでは、関連する
comment、docstring / JSDoc、README、canonical documentを同じdiffで確認し、staleになった説明を
更新または削除する。completionをcomment数、docstring数、文字数、README数、日本語文字率だけで
判定しない。

このpolicy段階でrepository-wideのREADME coverage、public symbol docstring / JSDoc coverage、
comment数、全suppression禁止をhard failureにしない。policy導入時に既存debtを即時hard failureへ
変換しない場合がある。coverage guardは、remediation completeness、false-positive risk、
maintenance costを確認してから導入する。README remediationとsource code documentation remediationは、
互いのscopeを先取りしない独立したreview unitに分離できる。

## repository-wide policyとsubproject固有policyの境界

独立core等のsubprojectは、対象contractと利用者に必要であればrepository-wide policyより厳格な
documentation policyを持てる。そのsubproject固有policyをrepository全体へ自動適用しない。
repository-wide defaultには、全module、全private helper、全property、全`__post_init__`の必須化や、
symbol countによる機械的な完了判定を採用しない。

一方、unit、frame、solver assumption、resource contract、failure behaviorを誤読させない原則は、
該当するAPIへcontract significanceに基づいて適用する。
