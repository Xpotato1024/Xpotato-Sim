---
status: canonical
owner: architecture
last_verified: 2026-09-28
canonical_for:
  - repository-local agent Skill governance
related:
  - AGENTS.md
  - docs/operations/codex-workflow.md
  - docs/operations/validation.md
  - docs/operations/git-pr-workflow.md
  - docs/operations/japanese-doc-writing-guardrails.md
  - docs/architecture/documentation-sot-policy.md
  - research/README.md
---

# repository-local Skill governance

この文書は、`Xpotato-Sim`のrepository-local Codex Skillに関するlifecycle、
evidence、autonomy boundary、validationの正本である。現在のtaskを完了することを
Skill改善より優先し、Skill systemは既存の編集、Git、GitHub、external service、
production、hardware権限を拡張しない。

## 責務の分離

| 媒体 | 所有する知識 |
|---|---|
| `AGENTS.md` | 常に適用する短い規則とSkill systemへのrouting |
| `.agents/skill-system.toml` | repository opt-in、threshold、autonomy boundary |
| `.agents/skills/` | 条件付きで再利用する1 job単位のworkflow |
| `.agents/skill-candidates/` | 反復性、再構成コスト、失敗防止価値のevidence |
| `.agents/skill-evals/` | trigger、route boundary、代表dry-runの評価条件 |
| `scripts/` | 決定的で壊れやすい処理。新設・重大変更は別のapproval boundary |
| `references/` | 詳細で比較的安定した参照情報 |
| test / CI / validator | 機械的に強制できる不変条件 |
| Issue / PR / state / research log / RAG | 現在状態、provenance、実験結果などの変動情報 |

Skill本文へcurrent Issue、branch、commit SHA、日付、local absolute pathを固定しない。
canonical documentationの詳細をSkillへ大量複製せず、正本へrouteする。

## Skillのinstruction-onlyとtaskのwrite boundary

`instruction-only`はSkill自身が手順を記述・検証するだけで、外部side effectを実行しないという意味である。
対象taskのread-only / write modeとは別であり、instruction-onlyを理由にすべてのplugin taskをread-onlyへ固定しない。

read-only taskでは、pluginのcontract、ownership、discovery、identity、registration、composition、docs、tests、impactを
監査するだけで、Skill関連ファイルやplugin実装を変更しない。対象Issueと`AGENTS.md`が明示的に許可するwrite taskでは、
plugin implementation、registration / catalog、discovery、plugin-owned resources、directly required tests、plugin-local README、
axis README、related canonical docsを変更してよい。ただしSkillは新しい権限を与えず、Issue scope外のpublic contract、重大なschema、
dependency追加、runtime compositionのmaterial expansion、unrelated viewer / simulation、compatibility layer、parallel implementation、
hardware、serial、OSC、production、credentials、external mutationは停止・承認対象とする。

validatorが検査するSKILL.md frontmatter subsetは、先頭と末尾の`---` delimiter、および
重複しない単純scalarの`name: value`と`description: value`の2 keyだけである。list、nested
mapping、任意のYAML tagは扱わない。`agents/openai.yaml`もYAML全仕様としてparseせず、
`policy.allow_implicit_invocation`のboolean宣言だけを検査する。

## Opt-inとscope

repositoryが`.agents/skill-system.toml`を持ち、`enabled = true`の場合だけ、この
governanceのcandidate記録やrepository-local Skillの作成・更新を許可する。設定の
schemaはvalidatorが検査できる最小構造に限定する。

自動作成・更新の対象は、repository内の`.agents/skills/**`に閉じたinstruction-only
Skillだけである。dependency、MCP、credentials、production、hardware、serial、OSC、
外部mutationを追加・実行しない。user-global Skill、`~/.codex/`、`~/.agents/`は変更禁止。
executable scriptの新設・重大変更、implicit invocationのrepository-wide強制、外部
side effectを持つSkillは、明示承認または専用Issueなしに行わない。

incidentally発生したSkill差分は、product変更と混在させず、可能なら独立commitまたは
follow-upへ分離する。stacked PR、hotfix、merge conflict中、並行branchで同じcandidateや
Skillを更新している場合は、Skill作成を止め、candidate記録または最終報告だけに留める。

## Candidate Reviewの発火条件

次のstrong signalを1件以上、またはcomplexity signalを2件以上、persisted evidenceから
観測した場合だけCandidate Reviewを行う。単にtaskが長い、難しい、fileが多いだけでは
発火しない。

Strong signal:

- 同一または構造的に同等なworkflowを2回以上確認した。
- 同種のreview指摘またはuser修正を複数回確認した。
- 既存Skillの未発火、誤発火、過剰適用、手順不足、stale reference、過剰作業を確認した。
- 同じscript、validator、template、checklist、reportを繰り返し再生成した。

Complexity signal:

- 7段階以上の順序依存手順がある。
- 6回以上のtool、shell、Git、GitHub操作を順序どおりに要する。
- 3個以上のdocument、code、Issue、PR、external sourceからworkflowを再構成する。
- 順序誤りがrollback、CI再実行、merge conflict、PR retarget、review差戻しにつながる。
- 再利用可能なinputs、outputs、Definition of Doneが明確である。

Evidenceはrepositoryのcandidate store、Git、Issue、PR、review履歴、task artifact、
またはuserが反復を明示した事実から取得する。cross-session recurrenceを推測しない。
レビュー履歴が存在しない場合は、存在しないことを証拠として明記し、reviewを捏造しない。

## Scoringと判断

候補は次の5軸を各0〜2点で採点し、`total`は合計値と一致させる。

| 軸 | 2点の条件 |
|---|---|
| `recurrence` | 複数の独立した実例で反復を確認できる |
| `reconstruction_cost` | 正本と状態を毎回再構成するコストが高い |
| `error_prevention` | 手戻り、権限逸脱、state破損を明確に防ぐ |
| `stability` | input、output、順序、責務が安定している |
| `verifiability` | 成否をtest、validator、stateで客観的に確認できる |

判定の目安は、0〜4点を`none`、5〜6点を`record`または`update`、7〜8点を
`create-draft`または既存Skillの`update`、9〜10点をvalidation後の`promote`検討とする。
thresholdは`.agents/skill-system.toml`で固定する。scoreは安全、permission、Issue scope、
repository policyを上書きしない。

## Status、action、lifecycle

候補の`status`と判断の`proposed_action`は別軸で記録する。

- status: `observed`、`candidate`、`draft`、`active`、`deprecated`、`rejected`
- action: `none`、`record`、`update`、`create-draft`、`promote`、`merge`、`disable`、
  `deprecate`、`approval-required`

`merge`はSkill同士の責務統合を意味し、Git / PR mergeを意味しない。lifecycleは
`observed`な事実をevidenceとして確認し、`candidate`を採点し、thresholdに応じてdraftを
作成する。draftはexplicit-onlyで構造、trigger、代表task、side-effect boundaryを検証し、
検証済みで安定している場合だけactive化を検討する。active化にはcandidate scoreが
`promotion_review` threshold以上であり、candidate、eval、policyが後述の条件を満たす必要がある。
stale、重複、obsoleteなSkillは
`update`、`merge`、`disable`、`deprecate`または`rejected`を選び、理由と残存riskを残す。
candidateを実装したSkillは`realized_by_skills`へ、責務が隣接・重複する別Skillは
`related_overlapping_skills`へ記録し、両者を混同しない。`draft`または`active`のcandidateは
1件以上のrealized Skillを持ち、未実装のrecord-only candidateは`realized_by_skills = []`の
`candidate` / `record`として維持する。

## Invocation policy

implicit invocationはmetadata matchによるworkflow selectionであり、permission grantではない。
prompt、対象Issue、`AGENTS.md`、canonical documentation、Git / GitHub、hardware safety、
external side-effect boundaryを変更または拡張しない。

新規Skillは原則としてexplicit-only draftで開始する。各Skillの
`agents/openai.yaml`に`policy.allow_implicit_invocation: false`を設定し、trigger eval、
代表task、required input、failure path、side-effect boundaryが検証されるまでimplicit
invocationを許可しない。validated activeなinstruction-only Skillは、repository設定が許可し、
未解決approvalがない場合に`allow_implicit_invocation: true`へ移行できる。validation完了後は
repository設定に従ってactive化とimplicit化を自動適用してよいが、active化は権限拡張を意味しない。
active implicit Skillはcandidate evidence、promotion threshold、eval、policyからdata-drivenに
導出する。validatorへSkill名やcandidate対応を固定せず、新しいSkillのactive化でvalidator
scriptの変更を要求しない。bootstrap Skillも例外扱いせず、少なくとも1件のactive candidateへ
追跡可能にする。

複数Skillが一致する場合は、taskの現在段階と主要目的に最も狭く一致するSkillをprimaryとして
優先する。関連Skillを無条件に連鎖発火させず、各Skillのrequired inputとtriggerを個別に確認する。
required inputまたはpermissionが不足する場合は、該当stepをNot Applicable、Not Run、または
`approval-required`として停止・報告する。誤発火、未発火、過剰適用はCandidate Reviewのstrong
signalとして扱う。

## Candidate schema

候補は`.agents/skill-candidates/<candidate-key>.toml`に1候補1ファイルで保存する。必須
fieldは`schema_version`、`candidate_key`、`status`、`scope`、`summary`、
`observable_evidence`、`realized_by_skills`、`related_overlapping_skills`、`approval_boundary`、
`unresolved_risks`、および`[score]`内の5軸と`total`である。`candidate_key`はfilenameと一致し、
repository内で重複させない。Issue番号、PR番号、SHA、日付は`observable_evidence`に限って
記録し、Skill本文の恒常手順には移さない。時間、token、コストの削減量は根拠なく記録しない。
`realized_by_skills`はcandidateを実装したSkill、`related_overlapping_skills`はcandidateの実装では
ない隣接・重複Skillを表す。同じSkillを両方へ記録せず、Skillとcandidateの実装対応を重複させない。
active化時はSkillの現在境界に合わせてcandidateの`approval_boundary`と`unresolved_risks`を同期する。

## Eval schema

各Skillに`.agents/skill-evals/<skill-name>.toml`を対応させる。必須fieldは
`schema_version`、`skill_name`、`invocation_policy`、`validation_status`、
`side_effect_policy`、`unresolved_approval`、`side_effect_boundary`、
`positive_triggers`（3件以上）、`negative_triggers`（2件以上）、`route_boundaries`、
`required_inputs`、`expected_major_steps`、`expected_outputs`、`forbidden_actions`、
`representative_dry_run`、`false_positive_risk`、`false_negative_risk`、
`stale_reference_risk`、`routing_cases`である。implicit対応Skillは
`invocation_policy = "implicit-after-validation"`、`validation_status = "validated"`、
`side_effect_policy = "instruction-only"`、`unresolved_approval = false`を満たす。
各routing caseは複数候補prompt、候補Skill、primary Skill、無条件連鎖禁止、permission非付与を
表現する。triggerは日本語promptを中心とし、negativeには非対象または別Skillへのroute例を含める。
stored `primary_skill`はdeterministic fixtureの期待値であり、実model selectionの実測結果ではない。

## Skill authoring contract

1 Skill = 1 jobとする。SKILL.mdはfrontmatterの`name`と`description`、trigger、exclusion、
required inputs、input acquisition、ordered workflow、permitted variation、outputs、
Definition of Done、failure handling、retry、stop / escalation condition、side-effect
boundaryを明示する。frontmatterはvalidatorが検査する小さなsubsetだけを使用し、YAML全仕様を
独自実装しない。placeholder、empty description、secretらしき値、mojibake、BOMは禁止する。

## Validationとpromotion

validatorはconfig、candidate / eval TOML、duplicate key、score total、status / action、
Skill frontmatter、directory/name、lowercase-hyphenated name、duplicate Skill、参照path、
implicit policy、validation status、approval、routing case、placeholder、transient state、
secretらしき値、UTF-8、BOM、mojibakeを検査する。
active candidateはscore totalが`promotion_review` threshold以上であり、repository設定が許可する
active後action、実在するrealized Skill、validated eval、implicit-after-validation、
instruction-only、未解決approvalなし、implicit policy true、permission grant falseを満たすことを
機械検査する。active Skill名とcandidate対応はcandidate storeから導出し、validator codeへ固定しない。
孤立したcandidate、Skill、eval、policy、typo、重複対応、未実装candidateのactive化を拒否する。
Skill本文のtransient SHAは、standaloneの40桁full SHAと、`commit`、`sha`、`head`、`base`の文脈に続く
7〜39桁のhex SHAを検出する。candidateのobservable evidenceとevalのrepresentative fixtureにあるSHAは許可し、
SHA形式そのものを説明するcanonical docsの一般例もSkill本文の検査対象外とする。
trigger evaluationではpositive 3件以上・negative 2件以上、route boundary、複数候補時の
最狭primary、無条件連鎖禁止、permission非付与を確認する。代表dry-runではmetadata / trigger
routing fixtureとして成果物、DoD、失敗時の停止条件を照合する。
deterministic routing fixtureのpassを実model dispatchの成功として報告しない。実model dispatchは
観測可能なinterfaceがない場合はNot Runとし、active化後の誤発火・未発火・過剰適用は今後の実taskで
継続評価する。

変更層、contract、failure mode、side effectに対するvalidation選択は
[`validation.md`](validation.md)を正本とし、全taskへfull suiteを機械適用しない。testsの削除、
skip、弱体化、未実行項目の成功扱い、simulation smokeのhardware validationへの読み替えはしない。
docsのlink、encoding、Japanese guardrail、Git diff、base / branch / head / PR metadataも
必要範囲で検証する。

promotion前にrequired inputの取得可能性、expected output、failure path、false positive / negative、
stale riskを再確認する。draft、未検証、未解決approval、side-effectful policyをimplicit化しない。
実行できないvalidationはNot Run Reason、代替証拠、残存riskとして報告する。

## 既存workflowへのroute

- Codex全体の入口とtask固有deltaは[`codex-workflow.md`](codex-workflow.md)へrouteする。
- 変更層とfailure modeの検証選択は[`validation.md`](validation.md)へrouteする。
- Git、PR、head一致、long-form bodyのtransport / structure gateは[`git-pr-workflow.md`](git-pr-workflow.md)へrouteする。
- 日本語、UTF-8、BOM、mojibake、PR bodyの安全規則は[`japanese-doc-writing-guardrails.md`](japanese-doc-writing-guardrails.md)へrouteする。
- 文書の配置、canonical role、Source of Truthは[`../architecture/documentation-sot-policy.md`](../architecture/documentation-sot-policy.md)へrouteする。
- research logとexperiment evidenceの要否は[`../../research/README.md`](../../research/README.md)へrouteする。

## Git / PR境界

Skill関連変更も通常のbranch、actual diff、commit、push、Draft PR gateに従う。merge、Ready化、
Issue close、branch削除、release、deploy、外部mutationは、既存workflowと明示承認なしに行わない。
Skill候補だけの変更は可能なら独立commitにし、product変更と混在した場合はdiffと最終報告で分離を明示する。

## 担当・再利用・復旧の継承

[Codex workflow](codex-workflow.md)をSkillにも適用する。参照先Skillの全手順を自動連鎖させず、失敗・不足・失効した範囲だけを確認する。通常実装の担当交代、model pin、全件再実行、全面fresh reviewを追加の完了条件にしない。必要な独立性・権限・Skill変更時の関連evalは維持する。
