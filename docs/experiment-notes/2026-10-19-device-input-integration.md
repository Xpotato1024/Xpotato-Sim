---
status: supporting
owner: runtime
last_verified: 2026-10-19
canonical_for: []
related:
  - docs/contracts/selfrionette-device-input.md
  - docs/contracts/finite-trial-runtime.md
  - docs/contracts/workbench.md
  - tests/runtime/test_device_force_integration.py
---

# Device正規化入力と接触力表示のsoftware統合

## 目的と今回の到達範囲

#631はSelfrionette-Deviceの共有Rust serviceが出す品質付き正規化入力を、既存の有限試行へ接続する。
Simでtare、neutral、校正、filterを再実装せず、明示した個体・profile・operator・source revisionを
検証した入力だけを共通`ModelExecution` / `TrialRunner` / Workbenchで消費する。
#629/#630の接触力信号・表示条件とも同じnative worldで組み合わせる。

本noteでは、native接触を含む再現可能な合成入力比較、実Workbenchと固定Viewer buildの操作観測、
pipe readerの局所測定を区別する。実board、serial接続、二台同時の実運転、参加者の操作、
firmware書込みやEEPROM更新は行っていない。software fixtureの個体ID・校正record・軸対応は合成値である。
契約そのものは[Device入力の正本](../contracts/selfrionette-device-input.md)へ置く。

## 単手・両手と表示条件の比較

`tests/runtime/test_device_force_integration.py`で、
`dynamic-fixed-contact-single-device` / `dynamic-fixed-contact-bimanual-device`それぞれを
`no_force_cue`と`force_cue`で180 native control tickずつ進める。
controlは1/60 s、physicsは1/600 s、`implicitfast` / `Newton` / 50 iterations / tolerance 1e-10。
既存Mappingのgain 0.02 m/s、上限0.05 m/s、tool sphere 10 mm、100 mm固定cube、
入力age上限0.2 sを維持する。新しい物理実行経路や試行中のqpos書換えは使わない。

最初の新規sampleは全channelのcommandを0にして試行開始後の中立gateを通す。
以後はleftのCH1を0.5、rightを0.25とし、残りを0に固定する。roleごとのsessionと
sequence、device timestamp、producer receipt、firmware / profile / operator identityを明示する。
ageは2 msとし、各tickで新しいsampleを共通runnerへ渡す。元eventは観測後にも完全一致で照合する。

既存condition editorの位置編集を使い、cubeの中心を単手では`[0.315, -0.246, 0.284] m`、
両手では`[0.315, 0.56, 0.46] m`へ固定した。単手と両手で初期手先位置が異なるためであり、
各モデル内の表示比較では同じ位置を使う。配布presetの保存値や接触閾値を測定に合わせて変更していない。

| 検証対象 | 観測・判定 |
|---|---|
| 表示条件間の入力・軌道 | 180 tickすべてで元Device event、qpos、qvel、simulation timeが一致 |
| rawと派生観測 | raw dynamics、手先×対象別反力、Taskがepoch以外で一致 |
| 接触成立 | 両モデルでnative反力の大きさが0.05 Nを超えるsampleを確認 |
| 両手の対応 | 左右の元入力と各endpointの反力を同じtick・simulation timeで照合 |
| 無接触・終端 | 無接触の有効ゼロと欠測を区別し、終端は元sample identityを保持したunavailable |
| 表示だけの編集 | 新condition hash・ticket・epochを発行し、native instanceとmodel build数1を維持 |

4件すべてが成功した。これは決めたsoftware入力と配置で表示条件が物理・入力の意味を変えないことの
証拠であり、実操作者の到達性、操作しやすさ、接触荷重の妥当性、cueの有効性を表す値ではない。

```sh
.venv/bin/python -m pytest -q tests/runtime/test_device_force_integration.py
```

## 実WorkbenchとViewerでの操作観測

実Workbenchのapplication transportへ、`selfrionette-bridge-event/v1`を出す専用の
Python stdio合成producerを接続した。producerは架空port名`software-ui-left` / `software-ui-right`だけを
受理し、serial libraryを使用しない。configure / status / connect / tare / neutral / apply / startの
返信と、開始後約80 Hzのsampleを生成する。このproducerのrateを実Rust・USB・ADCのrateと扱わない。

実行は`fast-arm-bimanual-device`、最大1200 tick、保存先を分けた有限試行。
同じWeb buildを`--web-dist`で固定した。操作用capabilityはprocess内で生成し`--control-stdin`へ渡し、
値を証拠へ保存しない。Chromium 134 headless、1500×1000、SwiftShaderによるsoftware描画である。

操作権取得→検証・準備→renderer ready→左右それぞれ4つの個別準備→操作画面→開始→STOP→
記録完了→同条件再試行と進めた。準備の前はStartが無効、全roleが適用済みになると有効になる。
初回のQAはlauncherのURL出力直後にHTTPがまだlistenしておらずnavigationが失敗した。
観測harnessに実HTTP応答の待機を加えてから再実行し、製品の起動完了とは区別した。

成功した観測では、runningのtick 62で7 channel×2 roleの14 meterを確認した。
CH1は左右とも0.100、CH2〜7は0.000、固定範囲は−1〜1で単位は無次元だった。
元sequence 159、device 2067 ms、profile record、tare epochが表示され、最も古い入力のageは16 msだった。
DOM取得と画像取得は異なる時刻で、同じtickの画像とは扱わない。

STOP後のtrial `10874454de014994bee8c7e886e6fdc4`は161 tick / 2.68333 simulation sで
`operator_abort` / `recording=complete` / `error=null`となり、両bridgeは`stopped`、ready=false、
入力meterは0件へ消去された。再試行はnative build数1のまま、新しいbridgeで準備し直すready=falseに戻った。
Deviceを選択したこの観測でbrowser Gamepad input送信は0件、browser page errorも0件だった。

この最初の成功観測はSim code `15e51a926dbe609a0f423f39c478935c89c90923`に、未commitの追加preset / testを
置いた状態で行った。選択した既存presetと製品codeは同commitの内容だったが、開始記録の
software revisionは`device-ui-software-observation`という観測ラベルであり、clean commitの証拠とはしない。
fontを用意していない初回画像では日本語glyphが欠けた。DOMの日本語文字列取得とUI操作は成功したが、
その画像だけで日本語の視認性を受入しない。

同じ固定buildへ測定環境だけのNoto Sans CJK JPをfontconfigで指定して追試した。
日本語の準備説明・操作button・channel表示を画像で確認した。trial
`c1cff8aede2b46c085f37f2aba9797f9`ではrunningのtick 61で14 meter、元sequence 154、device 2002 ms、
最古age 20 msを観測した。画像を取得した後のSTOPは237 tick / 3.95 simulation sで
`operator_abort` / `complete` / `error=null`。全bridge停止、meter消去、新規準備を要求する再試行、
native build数1、Gamepad送信0、page error 0を再確認した。取得間隔をinput latencyとは扱わない。
追試の開始記録は上記code commitに文書・追加testが未commitであることを示すrevisionラベルを持つ。

| 固定したViewerのidentity | SHA-256 |
|---|---|
| Viewer source | `c9fabb3142b97243407cd0635de2d40949fa5dbdae55d4e00849b08e77aaf74d` |
| `workbench-build.json`保存byte | `af0c742ec3a28e9f6b803f49d10e20677fde2c6af4d0585a558700c053d730ce` |
| 生成JavaScript bundle | `f36a8e5cb7e45cf90eec11d84dbca518f1c16dd3a3dd1aa77451a11d3d42729e` |

fontはQA用環境へだけ追加し、製品の依存・設定は変更していない。

## pipe readerのCPU仕事量

実装時に`Popen(..., bufsize=0)`のbinary `readline`が小さいreadを繰り返す可能性を検出した。
受信threadで行単位・上限付きに読む責務は維持し、製品を明示`bufsize=8192`へ変更した。
stderrは`read1(1024)`で到着済みの有界なchunkを受け取る。queue上限や元receipt・失効上限は緩めない。

局所比較では同じ1082 byteのsample行を2000件、合計2,164,000 byteのburstで出すchildを使い、
readerの`readline(65537)`で全行・byte一致を確認した。各buffer設定につき1回の観測である。

| binary pipe設定 | reader process CPU s | wall s | 全行一致 |
|---|---:|---:|---|
| `bufsize=0` | 0.7822 | 0.8005 | 2000 / 2000 |
| `bufsize=-1`（当該環境の既定8192 byte） | 0.0019 | 0.0178 | 2000 / 2000 |

測定環境はLinux 6.18.55 x86_64 / glibc 2.39 / CPython 3.12.14、`io.DEFAULT_BUFFER_SIZE=8192`。
fixture fileは1288 byte、SHA-256は`1bb1fe4cd2296851b484e55ba5adb45e1ad0d8d2af7d2a35ff1090455b2567ba`、
compact JSONとLFからなる1行はSHA-256 `d2b8b2ac8feca05f8a225cb014105752ca3cae6ec2173bab77508eb94e233a87`。
測定日は2026-10-18 UTCで、証拠の整理時刻は22:15:50 UTC。測定そのものの時分秒は採取していない。

この値はreaderのCPU仕事を局所的に減らせる根拠である。製品の明示8192設定をこの表で直接測定したとは扱わず、
同設定のbounded pipe / worker回帰を別に14件実施して成功した。
80 Hzで間隔を空けたproducer、Rust LiveService、JSON parse、trial execution、WebSocket、Viewerを含まないため、
全体latency・RTF・CPU改善率・実機の80 Hz成立へ外挿しない。再現用scriptは以下である。

```python
import json
from pathlib import Path
import subprocess
import sys
import time

sample = json.loads(Path("tests/fixtures/selfrionette_device/bridge-sample-v1.json").read_text())
line = (json.dumps(sample, separators=(",", ":")) + "\n").encode()
count = 2000
producer = (
    "import os,sys; chunk=bytes.fromhex(sys.argv[1]); n=int(sys.argv[2]); "
    "[os.write(1,chunk) for _ in range(n)]"
)
for buffering in (0, -1):
    before = time.process_time()
    started = time.perf_counter()
    child = subprocess.Popen(
        [sys.executable, "-c", producer, line.hex(), str(count)],
        stdout=subprocess.PIPE, bufsize=buffering,
    )
    seen = 0
    while True:
        raw = child.stdout.readline(65537)
        if not raw:
            break
        assert raw == line
        seen += 1
    child.stdout.close()
    assert child.wait() == 0 and seen == count
    print({"bufsize": buffering, "lines": seen,
           "reader_cpu_s": round(time.process_time() - before, 4),
           "wall_s": round(time.perf_counter() - started, 4)})
```

## 検証と残る受入

共通clock原点の仮定を排し、要求送信時刻を下限にした1回限りのclock対応を使う。
coalesced入力の順序には元producer receiptを使い、freshnessには保守的な最古receiptを使う。
sample ageの整数丸めが変わっても元受領順序を誤って拒否せず、表示再送で鮮度を更新しない。
部分準備、準備失敗、STOP、owner切断、再試行で元readyやprocessを再利用しないことを回帰で検査した。

architecture、既存Gamepad、有限試行、Device、反力、Workbenchの統合範囲は507件成功した。
この数に上記の追加接触4件は含めない。unit testやcompileの成功自体を研究実験やhardware acceptanceと呼ばない。
Viewerは`npm test`のTypeScript compileと33本のtest scriptがすべて成功し、production buildも成功した。
Deviceを持たない既存keyboard / Gamepadのnullableな表示fieldも互換回帰の完全一致期待値へ反映した。

実機に進むには、個体IDとfirmware / bridge build、保存profileの由来、7 channelの身体・軸対応、
無荷重tareと使用姿勢neutralの操作条件を確定する必要がある。ADC 10 / 80 SPSと出力80 Hzの対応、
実測latency、−65536の実source挙動、USB断・二台同時運転・Windows配布binaryは未測定である。
正式Evaluationとinput→sceneの連続evidenceは#583/#584へ残す。今回のsoftware到達を実機・参加者受入へ昇格させない。
