# 工程测试与发行检查

这些命令由开发者主动运行，不进入正常生成流程。需要 Python 3.10+、Pillow、NumPy 和现有 Node.js；源码包的 JavaScript 依赖须先按锁文件准备。内部真实诊断与旧开发目录不随公开包分发；可选历史对照必须明确跳过，不能依赖开发者电脑碰巧有旧文件。

## 本地回归

从源码根目录执行：

```text
python -B -m unittest discover -s engineering/tests -v
```

2026-10-24：从独立解压的源码 ZIP 按锁文件安装依赖，完整工程测试412项：401通过、11明确跳过、0失败/错误；未提供私有诊断或旧兄弟包。跳过项为可选历史材料及本机符号链接权限，详见 docs/RELEASE_VALIDATION.zh-CN.md。

记录操作系统、Python/Node/Pillow/NumPy、可用字体及通过/失败/错误/跳过明细。Windows 通过记录不等于 Linux 或不同字体也通过；跨字体标尺问题见 [KNOWN_ISSUES](../KNOWN_ISSUES.md)。不要删除真实失败断言或把未提供的历史资料伪造成夹具。

## 构建与独立校验

```text
python tools/build_release.py --output ../beta1-dist
python tools/verify_release.py "../beta1-dist/PPT Rebuilder King ChatGPT-20261024-Beta1.zip"
python tools/verify_release.py "../beta1-dist/PPT Rebuilder King-20261024-Beta1-source.zip"
```

构建输出为源码树外的新目录。构建器按白名单分开源码与运行包，生成版本清单及 SHA256SUMS；校验器检查归档路径、文件清单、长度、哈希及有限内容规则。两者不证明模型还原质量或公开权利；许可边界见根 NOTICE 和第三方许可清单。

`dependency/runtime-files.json` 固定认可依赖的393个文件及其长度、SHA256，并绑定未改动的锁文件。构建器只读取这些依赖文件；`npm ci` 产生的安装器快捷方式及上游开发文件不入包。必要文件缺失、内容变化或锁文件不符时拒绝构建，不自动联网安装，也不改写依赖代码。这样可从干净源码安装后重建同一运行包。

## 新 CLI 合成演练

先将构建得到的运行 ZIP 解压到独立目录，再执行：

```text
python tools/smoke_test.py --runtime "../runtime-unpacked/PPT Rebuilder King-20261024-beta1/compat-runtime" --work ../beta1-smoke-new
```

运行路径改为实际解压位置，`--work` 使用新的外部目录。脚本用程序生成的确定性 PNG 演练四个任务和真实 CLI、本地 PPTX 组装；角色核对为模拟宿主声明，不是人眼核对。它不调用图像服务，提供方调用或计费信息保持 null/unknown。公开四文件示例说明见 [examples/synthetic-basic](../examples/synthetic-basic/README.md)。

## 其他开发工具与回退

`snapshot_baseline`、`capture_fixture`、`inspect_job` 和 `replay_compare` 属于显式开发工具，分别用于快照、捕获、只读诊断和已有返回比较。它们不提供图像服务客户端或缺图兜底。`replay_compare` 的旧库 API 模式用于相应历史样本，不能替代新 CLI 的独立许可与角色绑定演练；失败或缺材料就保留不完整结论。

原输入、真实回图和在途作业保持原样。任何改动运行文件都要形成一致的新版本身份与发行清单，不能手改哈希绕过校验。回退让新作业使用保留的旧运行 ZIP；不把旧 state 强迁到新版本，不因此重生图片。
