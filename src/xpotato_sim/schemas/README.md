# schemas

## 責務

層間契約を定義する。型は`input`、`command`、`state`、`endpoint`、
`viewer_control`、`experiment_log`、`types`のwire domain単位で所有する。
package public surfaceは`schemas.__init__`の明示的な`__all__`を正とする。

## 入力

なし。schemas はどの層にも依存しない。

## 出力

各層が参照する immutable な contract 型。

`schemas.command`の`PhysicalOutputRequest`、`PhysicalOutputPermission`、
`PhysicalOutputDecision`は、内部commandと将来のphysical output境界を表す。K-preでは
serializationとpermission decisionだけを行い、通信やhardware side effectを持たない。

## 依存してよい層

なし。

## 依存してはいけない層

すべての実装層。

## 禁止事項

処理ロジック、MuJoCo 操作、通信、表示、入力読み取りを持たない。

1型1fileの旧moduleは退役済みであり、compatibility facadeとして再導入しない。

## canonical routing

- [schema contract](../../../docs/contracts/schemas.md)
- [dependency boundary](../../../docs/architecture/dependency-boundaries.md)
