# selfrionette package

Python package全体のhuman-facing入口である。public exportやschemaのregistryは各`__init__.py`と
canonical contractを正とし、このREADMEでは複製しない。

## layer routing

- [plugins](plugins/README.md): six-axis pluginとconcrete plugin追加
- [runtime](runtime/README.md): 唯一のmulti-layer composition root
- [schemas](schemas/README.md): immutable layer contract
- [kinematics](kinematics/README.md): generic solver Protocol
- [motion](motion/README.md): backend非依存のmotion生成
- [mujoco_backend](mujoco_backend/README.md): MuJoCo model / state / step adapter
- [transport](transport/README.md): payload delivery

layer ownershipと許可dependencyは
[dependency boundary](../../docs/architecture/dependency-boundaries.md)、
current compositionは[runtime composition](../../docs/architecture/runtime-composition.md)を正とする。
