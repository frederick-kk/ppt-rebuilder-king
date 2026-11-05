# 工程测试与发行检查

这些命令由开发者主动运行，不进入正常生成流程。工程回归需要 Python 3.10+、Pillow、NumPy 和现有 Node.js；源码开发的 JavaScript 依赖另行准备。内部真实诊断与旧开发目录不随公开包分发，可选历史对照缺失时应明确跳过。

## 本地回归

从源码根目录执行：

```text
python -B -m unittest discover -s engineering/tests -v
python -B -m unittest discover -s tools -p test_repack_runtime.py -v
```

2026-10-25 的本地工程回归为 415 项（404 通过、11 跳过），测试另使用了两个历史源码对照文件；这两个文件未随当时源码归档分发。同日独立解压源码回归为 429 项（418 通过、11 跳过、0 失败/错误），使用当时逐项核验的 393 个固定依赖文件。这些是历史环境的结果，不是本次发行整理新运行的统计。验证记录见 [仓库验收说明](https://github.com/frederick-kk/ppt-rebuilder-king/blob/main/docs/RELEASE_VALIDATION.zh-CN.md)。

记录本次实际操作系统、Python/Node/Pillow/NumPy、可用字体及通过/失败/错误/跳过明细。Windows 结果不代表 Linux 或不同字体通过；跨字体标尺问题见 [KNOWN_ISSUES](../KNOWN_ISSUES.md)。缺失的可选诊断不计为通过，不删除失败断言或伪造历史夹具。

## 构建与独立校验

先下载本版运行 ZIP，放在源码目录之外，例如 `../downloads/`。使用另一个新的外部目录存放构建结果：

```text
python tools/repack_runtime.py build --baseline ../downloads/PPT-Rebuilder-King-ChatGPT-20261105.zip --source . --output ../package-dist
python tools/repack_runtime.py verify --baseline ../downloads/PPT-Rebuilder-King-ChatGPT-20261105.zip --runtime ../package-dist/PPT-Rebuilder-King-ChatGPT-20261105.zip --source-archive ../package-dist/PPT-Rebuilder-King-20261105-source.zip
```

工具核对基线归档的路径、CRC、公开清单、固定的内部运行清单和快照，以及全部 423 个运行文件的内容；从基线复制运行文件，仅重新构建外层发行文件与独立源码归档。程序身份仍为 `PPT Rebuilder King-20261025-quality-test`，运行包根目录为 `PPT Rebuilder King`。本流程无需重新安装 npm 依赖，不重写内部代码、SKILL、依赖或 `MANIFEST.json`。

源码归档保留运行源码、开发工具、测试、说明和公开合成夹具，不携带 `node_modules`、私有诊断或内部历史材料。两类归档各有文件清单；构建输出另含配套项目指令和 SHA256SUMS。运行文件逐项不变不等于整个 ZIP 字节相同。文件核对不证明模型还原质量、提供方计费或公开权利；许可边界见 [NOTICE](../NOTICE.md) 和 [第三方许可](../THIRD_PARTY_LICENSES.md)。

## 新 CLI 合成演练

将本版运行 ZIP 解压到独立目录后执行：

```text
python tools/smoke_test.py --runtime "../runtime-unpacked/PPT Rebuilder King/compat-runtime" --work ../package-smoke-new
```

运行路径改为实际解压位置，`--work` 使用新的外部目录。脚本使用程序生成的确定性 PNG 演练四个任务和真实 CLI、本地 PPTX 组装；角色核对为模拟宿主声明，不是人眼核对。它不调用图像服务，提供方调用或计费信息保持 null/unknown。示例说明见 [examples/synthetic-basic](../examples/synthetic-basic/README.md)。

## 其他开发工具与回退

`snapshot_baseline`、`capture_fixture`、`inspect_job` 和 `replay_compare` 属于显式开发工具，分别用于快照、捕获、只读诊断和已有返回比较。它们不提供图像服务客户端或缺图兜底。`replay_compare` 的旧库 API 模式用于相应历史样本，不能替代新 CLI 的独立许可与角色绑定演练；失败或缺材料时保留不完整结论。

原输入、真实回图和在途作业保持原样。运行文件的行为变更需要一致的新版本身份与发行清单，不能手改哈希绕过校验。维护者如需本地回退，让新作业使用本地保留的旧运行 ZIP，不把旧 state 强迁到新版本，不因此重生图片。
