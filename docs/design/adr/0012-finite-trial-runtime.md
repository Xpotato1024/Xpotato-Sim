---
status: historical
owner: runtime
last_verified: 2026-09-29
canonical_for: []
related:
  - docs/contracts/finite-trial-runtime.md
---

# ADR-0012: アプリ寿命と有限試行の分離

## 背景

#591では、名前付きモデルpublisherに混在する構築、入力処理、Task判定、表示を分離する。
旧publisherの表示frame数をsimulation予算と読み替えると、入力待ちで試行が終了してしまう。
LaunchProfileの保存JSONは来歴であり、`dataclasses.replace`後の実効条件とは限らない。

## 決定

`runtime/execution/model_execution.py`へ通信非依存のモデル・入力・Task実行を抽出する。
旧publisherは従来のframe予算を指定してこの実行を呼び、新しい`runtime/experiment/trial_runner.py`は
同じ実行を有限trialの所有者として呼ぶ。腕数やkinematic/dynamic別のloopは作らない。
固定条件と結果保存は`runtime/experiment/`、CLIはthin entryとする。

初期APIは同期的な単一thread所有と非再入guardを持ち、別thread・再入・旧epochを拒否する。
非同期制御serverやGUIは#592であり、仮実装しない。準備の期限は同期buildから戻った時点でも検査し、
期限超過をreadyにしない。native callを強制中断するworker監督はこのAPIの保証ではない。

同条件ではmodelと安全なMjData作業域を再利用し、可変のSource/Mapping/Taskは再生成する。
旧epoch集合を無限に蓄積せず、現在epochとの一致で入口を検査する。結果は新規directoryと排他的file作成で
確定し、保存失敗は停止をラッチしてretryを禁止する。正式metric/replay artifactは#584に残す。

## 代替案と影響

GUI専用loop、旧LaunchProfileの黙示移行、旧one-cube評価manifestへの変換は採用しない。
新有限経路はv2/v3/v4の名前付きGamepadモデルを扱い、v1は理由付きで拒否する。旧v1 CLIは維持する。
現行契約・具体的な予算、記録、reset境界の唯一の正本は[有限試行契約](../../contracts/finite-trial-runtime.md)。
