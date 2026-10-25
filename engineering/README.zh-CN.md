# 工程测试与发行检查

这些命令由开发者主动运行，不进入正常生成流程。需要 Python 3.10+、Pillow、NumPy 和现有 Node.js；源码包的 JavaScript 依赖须先按锁文件准备。内部真实诊断与旧开发目录不随公开包分发；可选历史对照必须明确跳过，不能依赖开发者电脑碰巧有旧文件。

## 本地回归

从源码根目录执行：

```text
python -B -m unittest discover -s engineering/tests -v
```

本轮认可候选本地回归 415 项：404 通过、11 跳过，包含私下提供的两个历史源码对照文件。GitHub 独立干净源码回归统计另列于 [本版验收范围](../docs/RELEASE_VALIDATION.zh-CN.md)，不沿用旧版或私有环境的统计。

记录操作系统、Python/Node/Pillow/NumPy、可用字体及通过/失败/错误/跳过明细。Windows 通过记录不等于 Linux 或不同字体也通过；跨字体标尺问题见 [KNOWN_ISSUES](../KNOWN_ISSUES.md)。不要删除真实失败断言或把未提供的历史资料伪造成夹具。

本轮独立源码归档回归：**429 项，418 通过、11 明确跳过、0 失败/错误**；依赖为认可附件中逐项核验的 393 个固定文件，无私有诊断或旧兄弟包。此结果与认可候选的 415 项本地回归分开记录，详见 [验证范围](../docs/RELEASE_VALIDATION.zh-CN.md)。

## 构建与独立校验

```text
python tools/build_release.py --output ../quality1-dev-dist
python tools/verify_release.py "../quality1-dev-dist/PPT Rebuilder King ChatGPT-20261025-组件与文字交付修复测试版.zip"
python tools/verify_release.py "../quality1-dev-dist/PPT-Rebuilder-King-20261025-source.zip"
```

构建输出为源码树外的新目录。构建器按白名单分开源码与运行包，生成版本清单及 SHA256SUMS；校验器检查归档路径、文件清单、长度、哈希及有限内容规则。两者不证明模型还原质量或公开权利；许可边界见根 NOTICE 和第三方许可清单。

`dependency/runtime-files.json` 固定认可依赖的393个文件及其长度、SHA256，并绑定未改动的锁文件。构建器只读取这些依赖文件；`npm ci` 产生的安装器快捷方式及上游开发文件不入包。必要文件缺失、内容变化或锁文件不符时拒绝构建，不自动联网安装，也不改写依赖代码。这样可从同一干净源码与固定依赖重复构建开发制品。

主下载是认可原 ZIP 的冻结字节副本，沿用它自己的两层清单与历史外层结构；以上工具生成的是单独的开发重建格式，**不与主附件字节相同，也不替换主附件**。`tools/verify_release.py` 只支持开发重建格式。认可附件按 `RELEASE.json` 的 SHA256、原 `PACKAGE_MANIFEST.json` 与 `compat-runtime/MANIFEST.json` 核对，不关闭校验强行转换格式。

## 新 CLI 合成演练

先将构建得到的运行 ZIP 解压到独立目录，再执行：

```text
python tools/smoke_test.py --runtime "../runtime-unpacked/PPT Rebuilder King-20261025-quality-test/compat-runtime" --work ../quality1-smoke-new
```

运行路径改为实际解压位置，`--work` 使用新的外部目录。脚本用程序生成的确定性 PNG 演练四个任务和真实 CLI、本地 PPTX 组装；角色核对为模拟宿主声明，不是人眼核对。它不调用图像服务，提供方调用或计费信息保持 null/unknown。公开四文件示例说明见 [examples/synthetic-basic](../examples/synthetic-basic/README.md)。

## 其他开发工具与回退

`snapshot_baseline`、`capture_fixture`、`inspect_job` 和 `replay_compare` 属于显式开发工具，分别用于快照、捕获、只读诊断和已有返回比较。它们不提供图像服务客户端或缺图兜底。`replay_compare` 的旧库 API 模式用于相应历史样本，不能替代新 CLI 的独立许可与角色绑定演练；失败或缺材料就保留不完整结论。

原输入、真实回图和在途作业保持原样。任何改动运行文件都要形成一致的新版本身份与发行清单，不能手改哈希绕过校验。回退让新作业使用保留的旧运行 ZIP；不把旧 state 强迁到新版本，不因此重生图片。
