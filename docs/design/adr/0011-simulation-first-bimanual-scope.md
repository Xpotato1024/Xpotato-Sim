---
status: historical
owner: research
last_verified: 2026-09-25
related:
  - docs/architecture/research-execution-roadmap.md
  - docs/contracts/fast-arm-assembly.md
---

# ADR 0011: シミュレーション主経路と左右共通の実装範囲

## 来歴

利用者は2026-09-25の会話で、シミュレーション中心・実機は時間があれば・双腕希望の
研究相談結果を提示し、ばね課題を追加した計画で進めることを追認した。
論文repository PR #26のmerge `721c119537f51f582094fcb610b4b6bfa4349e67` に、
RM004と相談原文・追認原文・ばねプロトコルが保存されている。
その後、1スティックでXYZへアクセスする双腕入力を先にし、今回「逆腕は左右反転」「OSC等の
実ロボット用途も片側未実装に残さず、文書を確認して全体最適」と依頼された。
相談実施日や教員本人の逐語録をこの記録から認定しない。

## 確認した不整合

実装repositoryのroadmap（基点833c3fe）は、実機contactを最優先とし、Gamepadを
主比較対象にしない古い方針のままだった。これを新しい作業方針の障害にしないため、
現在のroadmapを更新する。過去ADR 0004と、既存実機Issueの受入条件は改稿しない。

## 判断

シミュレーション主経路と実機オプションの内部順序を分ける。前者は入力、片腕/双腕modelと
複数手先route、全sceneの接触、GUI/task/記録、予備実験へ進む。後者は従来の実機safety、
free-spaceからcontactへの手順、operator許可を保つ。
左右は共通arm/joint identityに結び、入力・状態・衝突・OSC target/応答・停止・ログまで
同じ受入matrixで扱う。core modelの鏡映は実機の符号・offset・限界を確定しない。

## 影響と限界

#569はmodel assemblyとnamed outputの最初の実装単位であり、双腕操作全体の完了ではない。
未完の複数手先runtime・連成出力・接触shape・GUI接続を明記し、右だけの成功を全体成功としない。
工程の実行順を更新しただけで、研究新規性、参加者数・閾値・統計の採択、参加者実験開始許可、
実機・serial/OSCの実送信許可を付与しない。

現在の実行順序は [研究実行ロードマップ](../../architecture/research-execution-roadmap.md) を参照する。
論文の作業基準は [保存済みRM004](https://github.com/Xpotato1024/Selfrionette-Mujoco-paper/blob/721c119537f51f582094fcb610b4b6bfa4349e67/research-memos/RM004_SimulationBimanualExperimentDesign.md)。
