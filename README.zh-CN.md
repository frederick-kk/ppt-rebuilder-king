# PPT Rebuilder King · Beta 1

将单页截图重建为对象可编辑的 PowerPoint：普通正文和支持的基础图形使用原生对象，艺术字、图标、纸卡及复杂插画使用独立图片。工作空间为 1920×1080，辅助尺为 25/50/5；辅助尺用于定位，不让对象吸附网格。

本版整理发行结构、文档、许可和可复验工具，保留已认可的生成与排版行为。它仍是 Beta，效果和耗时受输入、宿主图像工具、字体及运行环境影响。

## 选择下载内容

| 制品 | 用途 | 内容 |
|---|---|---|
| `PPT Rebuilder King ChatGPT-20261024-Beta1.zip` | 上传给支持文件处理和图像生成的宿主使用 | 当前程序、已核对的 JavaScript 依赖、SKILL、项目指令和运行清单 |
| `PPT Rebuilder King-20261024-Beta1-source.zip` | 阅读、开发、测试及重建发行包 | 源码、锁文件、工程测试、构建工具和公开合成示例；不携带 `node_modules` |

源码包不作为项目运行来源。使用运行 ZIP 时，上传该版本 ZIP，按根 `PROJECT_INSTRUCTIONS.txt` 设置项目指令；执行合同以包内 `compat-runtime/SKILL.md` 为准。每次输入使用新的独立作业目录，在同一作业目录中继续，保留暂停现场和已有回图。详细步骤见 [使用说明](docs/USAGE.zh-CN.md)。

## 开发与构建

需要 Python 3.10+、Pillow、NumPy 和可用的 Node.js。源码开发环境须自行准备依赖；构建工具不会自动联网安装：

```text
python -m pip install -r compat-runtime/requirements.txt
npm ci --prefix compat-runtime
python tools/build_release.py --output ../beta1-dist
python tools/verify_release.py "../beta1-dist/PPT Rebuilder King ChatGPT-20261024-Beta1.zip"
python tools/verify_release.py "../beta1-dist/PPT Rebuilder King-20261024-Beta1-source.zip"
```

输出目录须为源码目录之外的新目录。构建工具按白名单分别生成两个 ZIP、运行身份与清单、SHA256SUMS 及配套项目指令；不把内部诊断和历史个人日志装入公开包。开发命令与合成 CLI 检查见 [工程说明](engineering/README.zh-CN.md)。

## 能力与限制

- 当前默认流程使用不透明背景、真实透明前景、真实透明组件板和独立透明主标题；不以洋红去底作为新作业默认路径。
- 每个任务请求一次调用、一张图片。意外多回图只有在明确为一次调用、完整保存原件并逐图核对时，才顺序采用第一份可用结果；这不授权追加调用。
- 宿主须实际查看回图并按任务角色登记。文件绑定与角色声明不能证明服务端请求、计费或所有细节还原正确；真正失败不会被强制改成完成。
- 复杂艺术字图片可以移动、缩放，其内部文字不能逐字编辑；表格由基础对象组合，公式仅支持有限结构。
- 字体未匹配时会明确估算并限制文字框适配。Microsoft YaHei 不随包提供；标尺在不同字体下的边缘标签仍有限制，见 [已知问题](KNOWN_ISSUES.md)。

本轮在 Windows 的 Microsoft PowerPoint 16.0 中核对了认可缓存样本的对象与导出像素一致性；公开合成示例的原生文字、形状、线条和独立图片经过编辑、保存副本及重新打开验证。这只覆盖指定样本与环境，不等于所有页面、整体还原或所有 Office 编辑能力通过，详见 [发行验收范围](docs/RELEASE_VALIDATION.zh-CN.md)。Linux 的依赖、字体和显示结果需单独验证，本版不承诺跨字体完全一致或全平台 Office 编辑通过。宿主的模型和工具以实际可用能力为准，旧个人体验不作为当前模型排名。

## 验证状态

2026-10-24：从独立解压的源码 ZIP 按锁文件安装依赖，完整工程测试412项：401通过、11明确跳过、0失败/错误；未提供私有诊断或旧兄弟包。跳过项为可选历史材料及本机符号链接权限，详见 docs/RELEASE_VALIDATION.zh-CN.md。

公开 [合成示例](examples/synthetic-basic/README.md) 使用程序制作的 PNG 和模拟宿主声明，演练真实 CLI 与本地 PPTX 组装，不调用图像服务。它能说明技术结构与流程，不能充当真实截图还原、真人看图核对、提供方遥测或交付速度基准。计划任务数、实际报告调用数与端到端时间的区别见 [架构与验证边界](docs/ARCHITECTURE.zh-CN.md)。

## 许可与贡献

第一方源码、工程工具和随包文档采用 [MIT License](LICENSE)，保留原版权行 `Copyright (c) 2026 FrederikKK / frederick-kk`。第三方依赖适用各自许可，见 [NOTICE](NOTICE.md)、[第三方许可](THIRD_PARTY_LICENSES.md) 和 [依赖清单](dependency/SBOM.json)。输入图片、外部字体和生成素材不因第一方 MIT 自动获得再分发授权。

贡献方式见 [CONTRIBUTING](CONTRIBUTING.md)，版本变化见 [CHANGELOG](CHANGELOG.md)。
