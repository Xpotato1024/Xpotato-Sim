# AGENTS.md

Last updated: 2026-07-31

## 0. Purpose

このファイルは、`Xpotato-Sim`で作業するAIエージェント向けのrepository-local instructionである。

目的、Issue、関連するcanonical documentsを確認し、リポジトリ内の設計、tests、既存実装から必要な作業方法を判断する。恒常ルールを個別プロンプトへ重複転記しない。

## 1. Read first

作業前に、タスクに関係する範囲を確認する。

1. `AGENTS.md`
2. 対象タスクのIssueと依存Issue / PR（該当する場合）
3. `docs/README.md`のSource of Truth Map
4. 関連するcanonical architecture / contract / design / operations document
5. 関連実装とtests
6. `research/README.md`の更新条件と、実験を扱う場合は`docs/experiment-notes/README.md`

主要な入口:

- 開発方針: `docs/architecture/development-policy.md`
- code / plugin documentation: `docs/architecture/code-documentation-policy.md`
- architecture ownership: `docs/architecture/dependency-boundaries.md`
- runtime composition: `docs/architecture/runtime-composition.md`
- conventions: `docs/conventions.md`
- Git / PR workflow: `docs/operations/git-pr-workflow.md`
- validation: `docs/operations/validation.md`
- hardware safety: `docs/operations/hardware-safety.md`
- 日本語docs: `docs/operations/japanese-doc-writing-guardrails.md`
- research log: `research/README.md`
- experiment evidence: `docs/experiment-notes/README.md`

詳細な正本は`docs/README.md`を優先する。

## Repository-local Skill routing

- repository opt-inの正本は`.agents/skill-system.toml`とする。
- task開始時に利用可能なSkill metadataを確認し、validated active Skillはmetadata matchによりimplicitに使用してよい。Skill名の明示指定は必須ではない。
- active implicit Skillは、canonical governanceに従ってactive candidate evidence、eval、policyへ追跡可能であることを確認する。
- prompt、対象Issue、この`AGENTS.md`、canonical documentation、既存permission boundaryを常にSkillより優先する。
- draftまたはapproval未解決のSkillはexplicit-onlyとする。
- strong signal 1件、またはcomplexity signal 2件以上を観測した場合だけCandidate Reviewを行う。
- read-only taskではSkill関連ファイルを変更しない。
- current taskの完了をSkill改善より優先する。
- Skill改善によって既存の編集、Git、GitHub、external service、production、hardware権限を拡張しない。
- 詳細なrouting、lifecycle、schema、autonomy boundary、validationは`docs/operations/agent-skill-governance.md`を参照する。

## 2. Autonomy and permission boundary

説明、調査、レビュー、診断、計画では、依頼されていないファイル変更、commit、Issue / PR更新を行わない。read-only調査と非破壊的な検証は行ってよい。

修正、実装、作成を依頼された場合は、task / Issue scope内の変更、直接必要なtests、canonical docs、非破壊的検証を行ってよい。

次は明示許可を必要とする。

- merge、Issue / PR close、branch削除
- destructive migrationまたは大量削除
- material scope expansion
- public contractの重大変更
- 大幅なdependency追加
- deployment、secrets、credentials
- hardware access、serial open、Arduino upload、OSC送信、実機作動

## 3. Architecture invariants

以下を維持する。

- MuJoCoはphysical stateのsource of truthである。
- Three.jsはrenderingを担当し、独立したFK / IKまたは第二の姿勢SoTを持たない。
- 複数層のcompositionは`runtime/`が所有する。
- schemasはlayer contractであり、暗黙に破壊しない。
- `legacy/`は参照用であり、明示scopeなしに新実装からimportまたは実行しない。
- Rapier world / body / collider / joint / physics stepを新系統へ再導入しない。
- 旧PoseStateは必要な互換境界以外でSoTにしない。
- dependency boundaryはcanonical architecture docsと`tests/architecture/`を正とする。
- boundaryを変更する場合は、対応するdocsとtestsを同じ変更で整合させる。

過去のskeleton-first移行手順を、新しいすべてのIssueへ自動適用しない。現在のtask / Issue、canonical docs、既存実装から、必要な成果が調査、設計、実装、bug fix、validationのどれかを判断する。

新しい並行実装、互換層、stub、adapterは、それがtask / Issueの成功条件に必要な場合だけ追加する。

## 4. Documentation source of truth

ドキュメントは`docs/`に置き、`doc/`を新設しない。

- `docs/README.md`をSource of Truth Mapの正本とする。
- 1 topic = 1 canonical documentを守る。
- completion audit、handoff、過去Round文書を現在仕様の正本として扱わない。
- 新しい文書を追加する前に、既存canonical documentを更新すべきでないか確認する。
- 文書、実装、testsの主張を一致させる。

### Task impact gate

作業開始時と完了前に、変更ファイルの種類ではなくtaskの実質的な影響から、次の3項目を判定する。

1. **Documentation impact**
   - current behavior、architecture、contract、schema、model、operator procedure、evaluation design、Source of Truthが変わる場合は、関連canonical docsと必要なtestsを同じ変更で更新する。
   - 上記が変わらない場合は、無関係なdocs更新や履歴文書の現行化を行わない。
2. **Research log impact**
   - 研究で実行・評価できる対象、simulation / runtime / model / contractの研究上の能力、実験条件・評価指標・比較条件、研究上の解釈・優先順位・仮説・主張範囲、または実験可能性・妥当性・再現性が変わる場合は、当月の`research/logs/YYYY-MM.md`を更新する。
   - `AGENTS.md`、workflow、formatting、typo、metadata、documentation governance、CI / validator、repository hygiene、GitHub運用だけの変更は、上記の研究影響を持たない限り原則としてresearch log対象外とする。
   - 更新対象外の場合は、PR本文または最終報告へ簡潔な更新不要理由を記録する。詳細な判断基準は`research/README.md`を正とする。
3. **Experiment evidence impact**
   - experiment condition、model / fixture、実行command、観測結果を新規取得または変更した場合は、`docs/experiment-notes/`へ記録する。
   - unit test、CI、compile、typecheck、通常のsmokeを実行しただけでは、それ自体をresearch experimentとして扱わない。

### Human-facing language policy

人間が読むことを主目的とする成果物は、原則として日本語で作成する。

- 対象はroot / app README、CONTRIBUTING相当文書、AGENTS.md、`docs/`、`research/`、GitHub Issue / PR本文、implementation report、review / audit report、completion / final reportとする。
- 見出し、表、説明、受入条件、検証結果も日本語を基本とする。
- code identifier、API / schema field、CLI command / option、repository path / filename、external formal name、error / protocol literal、出典の短い引用は必要に応じて英語を維持してよい。
- 既存英語文書は、対象Issueのscopeに含まれる場合だけ移行する。repository-wideの既存英語負債だけを初回からhard failureにしない。
- 変更した人間向け本文はlanguage policyのvalidation対象とし、code block、path、identifier、formal nameを単純な文字比率で誤判定しない。
- encoding、BOM、mojibake、GitHub本文のtransport integrityは`docs/operations/japanese-doc-writing-guardrails.md`を正とする。

### Code and plugin documentation

comment、docstring、JSDoc、TODO / FIXME、commented-out code、suppression、README、plugin documentationの
詳細は`docs/architecture/code-documentation-policy.md`を正とする。

- 新しいdiscoverable pluginにはplugin-local READMEを同じPRで追加する。
- 新しいplugin axisを追加する場合、またはaxis責務を変更する場合はaxis READMEを同じPRで更新する。
- comment、docstring、JSDocは日本語方針とcanonical policyに従い、commented-out dead codeを残さない。
- materialなTODO / FIXMEは、owner、未完了内容、成立または削除条件を追跡可能にする。
- behavior、ownership、contract変更時は、関連comment、README、canonical docsを同じPRで同期する。
- completionをcomment数、docstring数、README数、文字数、日本語文字率だけで判定しない。

## 5. Scope discipline

task / Issueの目的と成功条件を優先する。

通常は完全なfile whitelistではなく、対象subsystemまたはtouch areaを作業範囲とする。直接必要なtestsとcanonical docsは同じ変更に含めてよい。

次に進む前に停止して報告する。

- 明示scope外の別subsystemに設計変更が必要
- task / Issueで承認されていないpublic schemaまたはcontract変更が必要
- 明示scope外のdependency追加またはCI workflow変更が必要
- task / Issueの目的を実質的に拡張する必要がある
- 既存SoT間に矛盾がある
- 安全な実装方針を一意に決められない

unrelated cleanup、無関係なrename、format-only churnを混ぜない。

## 6. Hardware and external side effects

明示的なhardware taskでない限り、以下を行わない。

- serial port open
- Arduino upload
- OSC send
- robot output
- hardware validation
- deploymentまたはcredential操作

dry-run、MuJoCo model load、forward、step、Web build、typecheckをhardware validationと呼ばない。

専用hardware taskでは`docs/operations/hardware-safety.md`に従い、operator gate、device / port、command、physical clearance、stop procedure、rollback、expected / observed outputを定義する。

## 7. Git and GitHub

- `main`へ直接commitしない。
- Codexがbranchを作る場合は原則`codex/`接頭辞を使う。
- repository-local Git / PR workflowに従う。
- PR作成または更新前に、base、branch、actual diff、working treeを確認する。
- PR報告前にlocal HEAD、remote branch HEAD、PR headの一致を確認する。
- PR本文とactual diff、validation、task / Issue scopeを一致させる。
- `mergeable: true`だけでmerge readinessを判断しない。
- 明示許可なしにmergeまたはIssue closeを行わない。
- Codex実行プロンプトを、明示依頼なしにIssue / PRコメントへ投稿しない。

### Unicode-safe long-form updates

GitHub Issue、PR、comment、discussionなど、非ASCII文字を含む長文を更新する場合は、次を守る。

- 更新前に最新revisionの完全bodyを取得する。truncated snippetを更新元にせず、full bodyまたはexact backupを取得できない場合は更新しない。
- 更新前bodyをUTF-8 backupとして保存し、既存body全体を置き換えるAPIでは変更箇所以外を完全に保持する。numbering SoT、parent Issue、長期履歴本文を要約や推測で再構成しない。
- 日本語を含む本文はUTF-8 body fileまたはUnicode-safe APIで送信する。Windows legacy code pageやlocale-dependent shell pipeを経由しない。
- write直前に完全bodyを再取得し、backup対象と一致することを確認する。更新中に別変更が入っている場合や、複数agentが同じbodyを並列更新している場合は停止する。
- 更新後に完全bodyを再取得し、newlineを正規化したうえで送信bodyとの文字列完全一致を確認する。expected non-ASCII phrase、U+FFFD、文字化け、意図しない`?`置換も検査する。
- read-back不一致、欠落、短文化、文字化けを検出した場合は次の更新へ進まず、exact backupからrollbackする。failed / expected / actual bodyの差分を保存し、原因を解消するまで再送しない。
- connectorまたはAPIにrevision controlがない場合も、write直前のfull-body再取得とbackup一致をconcurrency gateとする。
- exact read-backによるtransport integrityと、exact pre-update bodyに対するstructural preservationを独立したgateとして検証する。Read-back equality alone is insufficient. A body that was already malformed before transmission can pass exact read-back verification.
- numbering SoT、parent Issue、長期roadmap、historical ledgerのmetadata更新は、既定で`localized-update`として`scripts/repository/validate_github_body_structure.py`をwrite前に実行する。candidateはexact previous bodyへのnarrow replacementまたはpatch applicationで作り、文書全体を再構築しない。
- structural overrideはintentionalな構造差分だけを対象とし、encoding / corruption、one-line collapse、fence balanceのhard failureを回避できない。CLI / imported APIのどちらも明示承認、理由、保存済みunified diffを必須とする。
- before / after bodyは別filesystem objectとし、structural elementはfence外の実heading / header-plus-delimiter table blockから抽出する。diff evidence pathも両inputと同一またはaliasにしない。
- 古い正常backupから復旧する場合は、damaged latest bodyをcontent evidenceとして照合し、後続の正当なhistorical entryが欠落していないことをwrite前に確認する。
- numbering SoTとparent / roadmap Issueのlocalized updateでは`--profile protected-long-form`、対象固有の`--required-section`、必要な`--required-table-section`を指定する。before bodyがcollapsed、sentinel欠落、またはtable identity欠落の場合はhard failureとして通常更新を停止し、既知正常backupとの三者照合によるrecovery workflowへ切り替える。baseline health failureはstructural overrideで回避しない。

Issue / PR本文を変更した最終報告では、関連する場合に限り、update method、encoding、backup source、read-back検証、rollback要否、検査したnon-ASCII phrase、最終stateを記録する。同じ恒常ルールをtask promptへ全文転記せず、このsectionを参照し、事故リスク固有の差分だけを追加する。

## 8. Validation

変更した層とfailure modeに対応する検証を選ぶ。全タスクへ同じコマンドを機械的に適用しない。

必要に応じて次を組み合わせる。

- focused regression tests
- 関連test suite
- `tests/architecture/`
- compile / typecheck / build
- MuJoCo model load / forward / step smoke
- replay / dry-run
- docs link / encoding / mojibake check
- Git diff / PR metadata audit

実行できない検証を成功扱いしない。未実行理由、代替証拠、残存リスクを報告する。

testsを削除、skip、弱体化して変更を通さない。

## 9. Repository hygiene

- repository名、URL、docs pathでは`Xpotato-Sim`を使用する。
- generated artifacts、`node_modules/`、`dist/`、`.env.local`、secrets、local absolute pathをcommitしない。
- `assets/`、schema、fixture、log formatを変更した場合は、consumerとcanonical docsへの影響を確認する。
- 日本語MarkdownとテキストはUTF-8 without BOMを基本とする。
- 日本語docsまたはPR bodyを変更した場合は、専用ガードレールに従ってmojibakeを確認する。

## 10. Completion

完了を宣言する前に、task / Issueのsuccess criteriaを実測結果で確認する。

次のimpact判定を完了し、必要な更新または更新不要理由を残す。

- Documentation impact: 関連canonical docs / testsを更新したか、変更不要であることを確認する。
- Research log impact: 当月logを更新したか、`research/README.md`に基づく更新不要理由をPR本文または最終報告へ記録する。
- Experiment evidence impact: `docs/experiment-notes/`を更新したか、experiment条件・観測結果を扱っていないことを確認する。

最終報告は、関連する項目だけを簡潔に含める。

```text
Result
Changed scope
Validation
Documentation impact
Research log impact
Experiment evidence impact
Remaining risks or blocked items
PR / Issue links, when applicable
```

architecture impact、SoT impact、hardware、numbering、merge order、rollbackは、実際に関係する場合だけ追加する。

テンプレートを埋めたことではなく、要求された挙動、互換性、検証、リポジトリ状態が成立したことを完了条件とする。
