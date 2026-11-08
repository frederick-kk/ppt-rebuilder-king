# 工程测试与发行检查

这些命令由开发者主动运行。工程回归需要 Python 3.10+、Pillow、NumPy、Node.js 和运行目录声明的 JavaScript 依赖。源码归档中的 JavaScript 依赖需另行准备。可选私有诊断或历史对照缺失时，记录实际跳过原因。

## 本地回归

从源码根目录执行。

```text
python -B -m unittest discover -s engineering/tests -v
python -B -m unittest discover -s tools -p test_repack_runtime.py -v
```

当前运行包对应的 2026-11-08 隔离工程回归共 519 项，508 通过、11 跳过、0 失败、0 错误。公开源码的打包工具另有 17 项测试。执行新一轮验证时记录实际环境及各项统计，结果范围见 [发行验收说明](https://github.com/frederick-kk/ppt-rebuilder-king/blob/main/docs/RELEASE_VALIDATION.zh-CN.md)。

操作系统、Python、Node、Pillow、NumPy、可用字体和可选材料会影响结果。缺失材料对应的跳过项单独列出。跨字体标尺边界见 [KNOWN_ISSUES](../KNOWN_ISSUES.md)。

## 构建与独立校验

先下载本版运行 ZIP，放在源码目录之外，例如 `../downloads/`。构建输出使用新的外部目录。

```text
python tools/repack_runtime.py build --baseline ../downloads/PPT-Rebuilder-King-ChatGPT-20261108-flow-r1.zip --source . --output ../package-dist
python tools/repack_runtime.py verify --baseline ../downloads/PPT-Rebuilder-King-ChatGPT-20261108-flow-r1.zip --runtime ../package-dist/PPT-Rebuilder-King-ChatGPT-20261108-flow-r1.zip --source-archive ../package-dist/PPT-Rebuilder-King-20261108-flow-r1-source.zip
```

本版采用 `approved-complete-archive`。运行 ZIP 逐字节复制已审定归档，源码 ZIP 独立构建。工具核对路径、CRC、完整归档的 784 个成员、两层清单、425 个运行文件及其固定指纹，其中依赖文件为 394 个。

| 项目 | 本版值 |
|---|---|
| 运行根目录与程序身份 | `PPT Rebuilder King-20261108-flow-r1` |
| 运行 ZIP SHA256 | `9f98c26ecb055bb5eb1f1b8bfa9409be7e79bae108102a7bc50a33c91dbb6047` |
| 运行清单 SHA256 | `eac29a95575b276a90499a1b9918eba39f274c7acfe81071c3ef9cdf6e63a16f` |
| 运行源码快照 SHA256 | `7423a29d88ea651f05c25f892bfc858d42b72c64da4850dfec3681b35ea56f16` |

构建输出共五个附件，包括运行 ZIP、源码 ZIP、项目指令 TXT、还原提示词 TXT 和 SHA256SUMS。源码归档包含运行源码、锁文件、工程工具、测试、说明和公开合成夹具，按既有清单排除 `node_modules` 与运行 `MANIFEST.json`。各附件的最终文件哈希以配套 SHA256SUMS 为准。许可范围见 [NOTICE](../NOTICE.md) 和 [第三方许可](../THIRD_PARTY_LICENSES.md)。

## 新 CLI 合成演练

将本版运行 ZIP 解压到独立目录后执行。

```text
python tools/smoke_test.py --runtime "../runtime-unpacked/PPT Rebuilder King-20261108-flow-r1/compat-runtime" --work ../package-smoke-new
```

运行路径改为实际解压位置，`--work` 使用新的外部目录。脚本使用程序生成的确定性 PNG 演练真实 CLI 与本地 PPTX 组装，角色核对由模拟宿主声明提供。图像服务调用为 0，提供方调用或计费信息保持 null/unknown。示例范围见 [examples/synthetic-basic](../examples/synthetic-basic/README.md)。

## 其他开发工具与作业保存

`snapshot_baseline`、`capture_fixture`、`inspect_job` 和 `replay_compare` 属于显式开发工具，分别用于快照、捕获、只读诊断和已有回图比较。`replay_compare` 的旧库 API 模式对应其历史样本。当前 CLI 的调用许可、请求绑定和角色审阅需分别验证。

新作业采用 `bounded-recovery/1`。缺登记字段时补齐真实回执并继续登记同一文件，真实输出失败时仅执行控制器下发的修复任务，全作业最多追加一次调用。显式 `--diagnostics` 导出实际所选作业，目标父目录需已存在，文件系统需支持原子硬链接发布。保留原输入、真实回图和在途作业，旧作业沿用其原包与冻结策略。
