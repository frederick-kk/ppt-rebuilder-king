# 工程测试与发行检查

这些命令由开发者主动运行。工程回归需要 Python 3.10+、Pillow、NumPy、Node.js 和运行目录声明的 JavaScript 依赖。源码归档中的 JavaScript 依赖需另行准备。可选私有诊断或历史对照缺失时，记录实际跳过原因。

## 本地回归

从源码根目录执行。

```text
python -B -m unittest discover -s engineering/tests -v
python -B -m unittest discover -s tools -p test_repack_runtime.py -v
```

R28冻结源码的选定工程回归共246项，245通过、1项因私有材料未配置而跳过，0失败、0错误。本次公开源码另按97个模块隔离执行1191项，1096通过、95项因可选材料或历史对照未配置而跳过，0失败、0错误。打包工具21项通过。执行新一轮验证时记录实际环境及各项统计，结果范围见 [发行验收说明](https://github.com/frederick-kk/ppt-rebuilder-king/blob/main/docs/RELEASE_VALIDATION.zh-CN.md)。

操作系统、Python、Node、Pillow、NumPy、可用字体和可选材料会影响结果。缺失材料对应的跳过项单独列出。跨字体标尺边界见 [KNOWN_ISSUES](../KNOWN_ISSUES.md)。

## 构建与独立校验

先下载本版运行 ZIP，放在源码目录之外，例如 `../downloads/`。构建输出使用新的外部目录。

```text
python tools/repack_runtime.py build --baseline ../downloads/PPT-Rebuilder-King-ChatGPT-20261117-controller-r28.zip --source . --output ../package-dist
python tools/repack_runtime.py verify --baseline ../downloads/PPT-Rebuilder-King-ChatGPT-20261117-controller-r28.zip --runtime ../package-dist/PPT-Rebuilder-King-ChatGPT-20261117-controller-r28.zip --source-archive ../package-dist/PPT-Rebuilder-King-20261117-r28-source.zip
```

本版采用 `approved-complete-archive`。运行 ZIP 逐字节复制已审定归档，源码 ZIP 独立构建。工具核对路径、CRC、完整归档的455项、两层清单、425个运行负载文件和一份运行MANIFEST及其固定指纹，其中依赖文件为 394 个。

| 项目 | 本版值 |
|---|---|
| 运行根目录与程序身份 | `PPT Rebuilder King-20261117-controller-r28` |
| 运行 ZIP SHA256 | `12e512c8aeeff563f10c7915d54db77326b464c336d326b454dd6455e0f67399` |
| 运行清单 SHA256 | `6e3d8b2c4292b57056f1fe44e4280a4457b8a826b0a4b9aacd81795277659713` |
| 运行源码快照 SHA256 | `4956f18fe3a334922cf807e294c05f78214f1de0a806b7190e2a04d10fecc9cc` |

构建输出共五个附件，包括运行 ZIP、源码 ZIP、项目指令 TXT、还原提示词 TXT 和 SHA256SUMS。源码归档包含运行源码、锁文件、工程工具、测试、说明和公开合成夹具，按既有清单排除 `node_modules` 与运行 `MANIFEST.json`。各附件的最终文件哈希以配套 SHA256SUMS 为准。许可范围见 [NOTICE](../NOTICE.md) 和 [第三方许可](../THIRD_PARTY_LICENSES.md)。

## CLI合成演练

将本版运行 ZIP 解压到独立目录后执行。

```text
python tools/smoke_test.py --runtime "../runtime-unpacked/PPT Rebuilder King-20261117-controller-r28/compat-runtime" --work ../package-smoke-new
```

运行路径改为实际解压位置，`--work` 使用新的外部目录。脚本使用程序生成的确定性 PNG 演练真实 CLI 与本地 PPTX 组装，角色核对由模拟宿主声明提供。图像服务调用为 0，提供方调用或计费信息保持 null/unknown。该脚本覆盖兼容CLI与本地组装，R28顺序和去底流程另由对应测试模块核对。示例范围见 [examples/synthetic-basic](../examples/synthetic-basic/README.md)。

## 其他开发工具与作业保存

`snapshot_baseline`、`capture_fixture`、`inspect_job` 和 `replay_compare` 属于显式开发工具，分别用于快照、捕获、只读诊断和已有回图比较。`replay_compare` 的旧库 API 模式对应其历史样本。当前 CLI 的调用许可、请求绑定和角色审阅需分别验证。

R28工作流启动参数启用`bounded-existing-return-recovery/2`。缺登记字段时补齐真实回执并继续登记同一文件，真实输出失败时仅执行控制器下发的修复任务，每任务最多自动修复一次，全作业最多两次。显式 `--diagnostics` 导出实际所选作业，目标父目录需已存在，文件系统需支持原子硬链接发布。保留原输入、真实回图和在途作业，旧作业沿用其原包与冻结策略。
