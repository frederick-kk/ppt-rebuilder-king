# 源码包测试与可选历史材料

从源码包根目录运行。测试需要 Python 3.10+、Pillow、NumPy；涉及PPTX组装的用例还需要 Node.js 与运行目录声明的 JavaScript 依赖。测试不会调用图像服务，临时输入和输出由用例在独立临时目录生成。

```powershell
python -B -m unittest discover -s engineering/tests -v
```

这是完整本地测试入口，不是生图效果、跨字体像素一致性或真实端到端耗时认证。缺少可选材料时会列出跳过原因；必须同时记录环境、通过、失败、错误和跳过，不能只摘录通过数量。

## 包内必需的旧行为对照

`tests/fixtures/grid25-baseline/` 自带原grid25最小源码快照、原MIT许可和成员SHA来源。`test_native_assets.py` 的背景规范化、原图/工作图/标尺和变换比较，以及 `test_native_prompts.py` 的旧提示词比较，均使用该目录。无需在源码包旁边放置任何旧ZIP或旧版本目录。

原有比较断言完整保留。旧source及其ruler/storage以独立模块命名空间加载，不复用当前生产模块。夹具丢失会失败，不能为了通过而跳过；该目录属于源码测试资产，不进入精简运行发行包。无需下载或复制字体文件。

可单独复核这两组：

```powershell
python -B -m unittest discover -s engineering/tests -p test_native_assets.py -v
python -B -m unittest discover -s engineering/tests -p test_native_prompts.py -v
```

## 可选真实诊断与更早历史对照

以下变量默认不设置。它们由测试操作者指向自己有权使用的私有材料，测试不会搜索下载目录，也不自动获取图片。不要把材料、个人绝对路径或原始作业塞入公开测试夹具来消除跳过。

| 变量 | 内容与覆盖 |
|---|---|
| `PRK_REAL_TITLE_DIAGNOSTIC` | 真实标题诊断ZIP，用于标题缩放和生成纪律回归 |
| `PRK_NATIVE_FAILURE_DIAGNOSTIC` | 原生透明失败诊断ZIP，用于回图来源保护 |
| `PRK_NATIVE_MISSING_DIAGNOSTIC` | 组件缺失诊断ZIP，用于透明边界恢复及原生文字溢出 |
| `PRK_TEXTFIT_BUDGET_DIAGNOSTIC` | 数字估宽预算诊断ZIP |
| `PRK_TITLE_RETRY_EVIDENCE` | 已解压的标题重试证据目录 |
| `PRK_COMPONENT_PLACEMENT_EVIDENCE` | 已解压的组件放置证据目录 |
| `PRK_FROZEN_RUNTIME` | 更早独立运行时的newpath目录，仅供标尺测试中的可选历史比较 |

`PRK_PACKAGE_UNDER_TEST` 是部分工程测试的显式包选择参数，不是运行依赖，也不应默认指向开发者个人目录。完整源码测试通常不需要设置它。

## 标尺字体的Beta限制

25/50/5网格与当前默认字体路径保持原样。标尺标签使用已安装字体，字体宽度不同会影响右端1900标签碰撞和内部保留区。现有测试期望不因发布整理而放松或改为跳过。

本机Arial 14的原有像素契约通过；Pillow默认字体和较宽字体可复现内区或标签问题。这属于跨字体已知限制，不应把某台机器的通过记录称作跨平台像素完全一致。公开源码保留失败可见性；正式支持更多字体环境前，需要另行批准和复验窄范围修复，不能调整网格数字掩盖字体问题。
