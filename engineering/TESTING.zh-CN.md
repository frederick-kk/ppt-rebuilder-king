# 源码包测试与可选历史材料

从源码包根目录运行。测试需要 Python 3.10+、Pillow、NumPy，涉及 PPTX 组装的用例还需要 Node.js 与运行目录声明的 JavaScript 依赖。测试的图像服务调用为 0，临时输入和输出由用例在独立临时目录生成。

```text
python -B -m unittest discover -s engineering/tests -v
python -B -m unittest discover -s tools -p test_repack_runtime.py -v
```

工程用例与打包工具用例分别统计。记录实际环境、通过、失败、错误和跳过明细。视觉效果、跨字体像素表现与真实端到端时间需要对应样本的实际验收。

## 包内基线与当前照片合同

`tests/fixtures/grid25-baseline/` 提供 grid25 最小源码快照、MIT 许可和成员 SHA 来源。`test_native_assets.py` 用其独立模块命名空间比较背景规范化、原图、工作图、标尺和变换。夹具缺失会使必需比较失败。

`test_native_prompts.py` 核对传统色键模式与原生透明模式共用的页面角色前缀和 1920×1080 几何信息，并继续逐字比较非照片组件板与独立主标题的基线提示。当前背景及前景提示采用完整照片合同，独立照片、截图、扫描件和拼贴图作为完整矩形前景保留，图内文字、标志和人物保留在图片中。

`test_embedded_photo_contract.py` 通过真实布局、组件板和请求构造，检查 `embedded_image` 标记在两种模式下的传递、矩形角色、图内文字归属和主标题选择，同时核对相同参考输入下的像素结果。原生素材用例继续检查 RGBA、透明边界、定位与像素几何。

这些夹具属于源码测试资产，也可存在于完整运行附件的配套工程资料中。发行运行附件共 784 个成员，运行清单包含 425 个文件。开发测试由操作者显式运行。

可单独复核以下用例。

```text
python -B -m unittest discover -s engineering/tests -p test_native_assets.py -v
python -B -m unittest discover -s engineering/tests -p test_native_prompts.py -v
python -B -m unittest discover -s engineering/tests -p test_embedded_photo_contract.py -v
```

## 流程恢复与诊断

`test_bounded_flow_recovery.py` 检查真实登记补齐、受限修复预算、已有合格结果复用、停止指令和旧作业策略。`test_job_diagnostics.py` 检查显式导出、所选作业索引、缺失证据和只读保护。`test_runtime_identity.py` 检查运行身份、固定清单与 CLI 作业目录约束。

## 可选真实诊断与更早历史对照

以下变量默认不设置。它们由测试操作者指向自己有权使用的私有材料。公开夹具只携带授权的合成材料和基线源码。

| 变量 | 内容与覆盖 |
|---|---|
| `PRK_REAL_TITLE_DIAGNOSTIC` | 真实标题诊断 ZIP，用于标题缩放和生成纪律回归 |
| `PRK_NATIVE_FAILURE_DIAGNOSTIC` | 原生透明失败诊断 ZIP，用于回图来源保护 |
| `PRK_NATIVE_MISSING_DIAGNOSTIC` | 组件缺失诊断 ZIP，用于透明边界恢复及原生文字溢出 |
| `PRK_TEXTFIT_BUDGET_DIAGNOSTIC` | 数字估宽预算诊断 ZIP |
| `PRK_TITLE_RETRY_EVIDENCE` | 已解压的标题重试证据目录 |
| `PRK_COMPONENT_PLACEMENT_EVIDENCE` | 已解压的组件放置证据目录 |
| `PRK_FROZEN_RUNTIME` | 更早独立运行时的 newpath 目录，供标尺测试中的可选历史比较 |

`PRK_PACKAGE_UNDER_TEST` 是部分工程测试的显式包选择参数。完整源码测试通常无需设置。

## 标尺字体的 Beta 限制

标尺标签使用已安装字体，字体宽度会影响右端 1900 标签碰撞和内部保留区。保留现有网格、字体和像素断言，并如实记录支持环境。

已有 Arial 14 像素契约检查与 Pillow 默认字体、较宽字体的边界问题属于各自环境的结果。正式支持新的字体环境前，需对对应样本独立复验，保留失败可见性。
