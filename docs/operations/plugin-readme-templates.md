---
status: supporting
owner: architecture
last_verified: 2026-07-30
canonical_for: []
related:
  - docs/architecture/code-documentation-policy.md
---

# plugin README template

この文書は
[`code-documentation-policy.md`](../architecture/code-documentation-policy.md)を適用するためのsupporting
templateである。`<replace-with-...>`は実在するidentityではなく、使用時に必ず置き換えるplaceholderである。
不要なoptional sectionは削除し、空sectionを残さない。

READMEはlocal entry pointとcanonical routingを担う。plugin declaration、catalog、schema、architecture
contractを複製したsecond SoTを作らない。identity、contract、parameterのcurrent値はcanonical ownerへ
linkする。

plugin READMEのactual rootは`src/xpotato_sim/plugins/`である。このtemplateからroot直下へ
別の`plugins/`を作らない。

## plugin root README

target: `src/xpotato_sim/plugins/README.md`

required section:

````markdown
# <replace-with-plugin-system-name>

## 目的

<replace-with-plugin-system-entry-point-and-six-axis-composition-summary>

## identityとdiscovery

<replace-with-logical-identity-and-bounded-discovery-summary>

canonical declaration: [<replace-with-declaration-label>](<replace-with-relative-link>)

## axis ownership

<replace-with-production-axis-and-generic-only-axis-routing>

## architectureとcontract

- [<replace-with-architecture-owner>](<replace-with-relative-link>)
- [<replace-with-contract-owner>](<replace-with-relative-link>)

## 追加時の入口

<replace-with-axis-selection-and-validation-route>
````

optional section:

- composition上のnon-goal
- application-facing selectionへのrouting
- generic-only axisがproduction catalog / runnerを持たないcurrent state

generic-only axisをproduction-readyと書かない。current concrete plugin listはcatalogのsecond registryになるため
列挙せず、catalog / discovery ownerへlinkする。

## axis README

target: `src/xpotato_sim/plugins/<axis>/README.md`

required section:

````markdown
# <replace-with-axis-name>

## 責務

<replace-with-axis-responsibility>

## 置けるもの / 置けないもの

- 置けるもの: <replace-with-allowed-responsibility>
- 置けないもの: <replace-with-excluded-responsibility>

## contractとI/O

- required contract: [<replace-with-contract-label>](<replace-with-relative-link>)
- input: <replace-with-input-semantics>
- output: <replace-with-output-semantics>

## lifecycleとside effect

<replace-with-lifecycle-and-side-effect-boundary>

## catalog / discovery / registration

<replace-with-current-owner-and-fail-closed-behavior>

## shared private owner

<replace-with-shared-private-owner-or-explicit-none>

## concrete pluginの追加

<replace-with-entry-point-readme-and-validation-steps>

## canonical document

- [<replace-with-architecture-owner>](<replace-with-relative-link>)
- [<replace-with-contract-owner>](<replace-with-relative-link>)
````

optional section:

- command semantics route
- compatibility boundary
- generic-only axisのcurrent limitation
- hardware accessを持つplugin向けoperator gate

generic-only axisは、generic contractが存在することとproduction concrete plugin、catalog、runner / UIが
存在することを分離して記述する。

## concrete plugin README

target: `src/xpotato_sim/plugins/<axis>/<plugin>/README.md`

required section:

````markdown
# <replace-with-plugin-display-name>

## 意味とresponsibility

<replace-with-plugin-semantics-and-local-responsibility>

canonical declaration:
[<replace-with-plugin-declaration-label>](<replace-with-relative-link-to-declaration>)

## input / outputまたはcomposition role

<replace-with-input-output-or-composition-role>

## parameters

<replace-with-parameter-owner-and-material-interpretation-or-explicit-none>

## lifecycleとside effect

<replace-with-start-stop-state-and-side-effect>

## compatibilityとcomposition

<replace-with-compatibility-and-composition-boundary>

## constraintsとnon-goals

- constraint: <replace-with-material-constraint>
- non-goal: <replace-with-material-non-goal>

## tests / validation

- [<replace-with-test-or-operation-label>](<replace-with-relative-link>)

## canonical architecture / contract

- [<replace-with-architecture-owner>](<replace-with-relative-link>)
- [<replace-with-contract-owner>](<replace-with-relative-link>)
````

optional section:

- command semantics route
- hardware / transport boundary
- resource contract
- failure / rejection / hold behavior
- deprecation / retirement condition
- task / environment / evaluator固有のcomposition条件

command semantics routeは、Control Mappingがrouteを宣言する、Robot providerがtyped Robot command
semanticを受理する、native command passthroughを持つ、またはselected routeがplugin behaviorを
実質的に変える場合だけ追加する。Environment、Task、Evaluation等へ一律に要求しない。

hardware accessまたは外部side effectがある場合は、device / portをREADMEへ固定せず、operator gate、
open / close lifecycle、defaultでside effectを起こすか、failure時の安全な状態、canonical hardware
operationへのlinkを記述する。hardware validationをsoftware-only smokeと混同しない。
