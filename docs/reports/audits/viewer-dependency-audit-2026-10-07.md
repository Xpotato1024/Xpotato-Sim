---
status: historical
owner: dependencies
last_verified: 2026-10-07
canonical_for: []
related:
  - docs/contracts/workbench.md
  - docs/operations/backend-viewer-startup.md
  - docs/operations/validation.md
---

# #618: Viewer依存auditと固定buildの検証

## 基点と範囲

main b233e3813977ebcc512aca56f69086c80bca2718、LLM-01 Windows、Node24.14.0/npm11.9.0で調査した。
#615/#619、起動整理PR620/621、recorder follow-up #617から分離する。
2026-10-07の[audit before JSON](viewer-dependency-audit-2026-10-07-before.json)は
脆弱なpackage6件（low1/moderate1/high4/critical0）、advisory9件だった。
独立CVE6件や実際の悪用を意味しない。[after JSON](viewer-dependency-audit-2026-10-07-after.json)は0件。
この時点のregistry/advisoryに対する結果であり、将来の安全を保証する値ではない。

## advisory・経路・必要な修正版

| package / 依存経路 | advisory ID | 指摘の条件 / 最小修正版 |
| --- | --- | --- |
| @vitejs/plugin-react → @babel/core → helper-compilation-targets → browserslist → baseline-browser-mapping | [GHSA-w5vr-8v7q-w6rv](https://github.com/advisories/GHSA-w5vr-8v7q-w6rv) | 不正なquery入力でprocess終了。2.11.0以上 |
| 同上browserslist | [GHSA-c83g-rgw3-j3cx](https://github.com/advisories/GHSA-c83g-rgw3-j3cx)、[GHSA-73wf-gq98-2v4g](https://github.com/advisories/GHSA-73wf-gq98-2v4g) | 多種queryのcache肥大/custom statsの不正入力。4.28.7以上 |
| vite → esbuild | [GHSA-g7r4-m6w7-qqqr](https://github.com/advisories/GHSA-g7r4-m6w7-qqqr) | Windowsのesbuild独自servedir serverでpath逸脱。0.28.1以上 |
| vite → postcss → nanoid | [GHSA-28wg-ghj8-5hjv](https://github.com/advisories/GHSA-28wg-ghj8-5hjv)、[GHSA-2v37-7h3g-55p8](https://github.com/advisories/GHSA-2v37-7h3g-55p8) | non-secure/custom generatorへの負/zero size。3.3.18以上 |
| vite → postcss | [GHSA-fxqj-rqcc-2cmp](https://github.com/advisories/GHSA-fxqj-rqcc-2cmp)、[GHSA-r28c-9q8g-f849](https://github.com/advisories/GHSA-r28c-9q8g-f849) | 不正CSSのprevious sourceMappingURLによるmap読込み。8.5.23以上 |
| vite → postcss → source-map-js | [GHSA-68fv-2mgg-jv7q](https://github.com/advisories/GHSA-68fv-2mgg-jv7q) | 不正なindexed source-map section offsetでevent-loop停止。1.2.2以上 |

全6packageはdev-onlyのtransitive依存だった。固定buildの通常起動はPythonが検証済み静的資産を配信し、
Node/Babel/PostCSS/esbuildを起動しない。明示dev-serverとbuildはrepoのJS/CSS/configを処理するので、
静的配布と同じ到達性とは扱わない。不正なsource/query/mapをこれらtoolへ渡した場合の境界は残る。

現行browserのGamepad/WebSocket frame処理からNode compilerの入力へ転送する経路はない。
ViteのNode codeはesbuild transform/buildを使い、repoはesbuildの独自serve/servedirを呼ばない。
従って当該Windows server条件をVite serverの実測脆弱性と断定しないが、tool自体もpatched版へ更新する。
これは現行sourceとtool呼出しの調査からの到達性判断で、侵入テストではない。

## lock更新の判断

既存semver範囲で対象7packageを指定したnpm update --package-lock-only --ignore-scriptsを使った。
unconditional audit fix、override、新規direct dependency、major更新を導入していない。
package.jsonとlockのroot依存宣言は同一のまま。Vite7.3.5はesbuildを^0.27.0へ制限するため、
0.28.1以上を許可する7.3.7のpatchへ更新した。esbuildの0.x minor変更はcompile/testで確認する。

Browserslistのdata/tool4packageも新しい宣言rangeを満たすため必要だった。
旧caniuse-lite/electron-to-chromium/node-releases/update-browserslist-dbは全て新rangeを満たさないことをsemverで確認した。
26個の@esbuild platform binary entryはesbuild本体と同じversion/integrityへ同期する。
変更はこの依存closureの37entryだけで、packageの追加・削除はない。

| package | before | after |
| --- | --- | --- |
| baseline-browser-mapping | 2.10.37 | 2.11.27 |
| browserslist | 4.28.2 | 4.29.3 |
| caniuse-lite | 1.0.30001799 | 1.0.30001815 |
| electron-to-chromium | 1.5.375 | 1.5.449 |
| esbuild | 0.27.7 | 0.28.2 |
| nanoid | 3.3.12 | 3.3.20 |
| node-releases | 2.0.47 | 2.0.57 |
| postcss | 8.5.15 | 8.5.29 |
| source-map-js | 1.2.1 | 1.2.2 |
| update-browserslist-db | 1.2.3 | 1.3.3 |
| vite | 7.3.5 | 7.3.7 |

@mujoco/mujoco、react/react-dom、threeのlock entryは同一。Python uv.lock、physics/input/Taskのsourceも変更しない。
MuJoCo WASM2fileのSHA-256はbefore/afterで同一だった。
API/保存形式/評価、#581寸法、#583評価定義、#584研究条件・人数を更新しない。
本変更はtool依存の補修であり、新しいPhase/Roundやresearch log/experiment noteは作らない。

## 検証と未確認

- npm ci、Viewer tests/typecheck/build、full audit0、production-only audit0が成功。
- 既存just setupで外部の所有済み固定buildを作成し、just doctorでPython/lock/Web依存/source/build identityが全て一致。
- 正式Workbench CLIの固定build startup-checkはloopbackでready/cleanup・exit0。使用portの残存listenerなし。profile/条件の選択、Start、browserを行わず、result rootに試行artifactを生成していない。
- 最終HEADのCIと独立レビューはDraft作成後に追記する。未確認を成功扱いしない。
- browser実操作、実機/serial/OSC、実験端末のAC接続下反復、侵入テストは未実施。
- registry/advisoryが変わったとき、lock更新時、build/source identity不一致時に再audit/検証する。
- network/security設定、credentials、権限、監査抑制設定を変更しない。
