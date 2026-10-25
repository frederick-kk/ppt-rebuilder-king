# PPT Rebuilder King · 2026.10.25-quality.1

将单页截图重建为对象可编辑的 PowerPoint：普通正文和支持的基础图形使用原生对象，艺术字、图标、纸卡及复杂插画使用独立图片。工作空间为 1920×1080，辅助尺为 25/50/5；辅助尺用于定位，不让对象吸附网格。

本版为「组件与文字交付修复测试版」，改善透明组件提取及缺字体时普通 ASCII 空格的宽度估算。沿用当前生成路线，保留正文原文、原布局与最大 25% 字号缩减限制。测试与个人反馈不构成所有页面的效果或耗时保证。

## 下载后直接上传 ChatGPT

- **[① 下载运行 ZIP](https://github.com/frederick-kk/ppt-rebuilder-king/releases/download/v2026.10.25-quality.1/PPT-Rebuilder-King-ChatGPT-20261025.zip)**
- **[② 查看 / 下载配套项目指令](https://github.com/frederick-kk/ppt-rebuilder-king/releases/download/v2026.10.25-quality.1/PPT-Rebuilder-King-20261025-PROJECT-INSTRUCTIONS.txt)**

运行 ZIP 下载后 **不用解压或改名，直接整包上传到 ChatGPT 网页版项目的「来源」**。下载附件名为 `PPT-Rebuilder-King-ChatGPT-20261025.zip`；它是已认可原运行 ZIP 的字节复制，包内身份仍为 `PPT Rebuilder King-20261025-quality-test`。仓库目前为 Private，只有已获权限的账号可以访问下载。

项目指令也可 [在线查看](docs/CHATGPT_PROJECT_INSTRUCTIONS.txt)，打开 Raw 后复制全部正文。此副本在原指令前补充了 GitHub 下载文件名对应关系，执行正文和生成规则保持原样；运行包本身未改动。

1. 从上面的链接下载运行 ZIP，打开配套项目指令 TXT。
2. 在 ChatGPT 网页版新建项目，记忆设置选择「仅限本项目」。
3. 上传运行 ZIP 到项目「来源」，将配套指令全文粘贴到「项目设置」→「项目指令」并保存。
4. 在项目内新开对话，选择具备文件处理、代码执行和生图能力的模型，上传一张单页高清图，并发送：

   > 请读取本项目指定的 ZIP，按项目指令和包内工作流，将这张截图还原成可编辑 PPTX。

5. 下载返回的 PPTX，用 WPS 或 PowerPoint 打开并检查。

这是 ChatGPT 网页版项目的使用方式，无需在本地 Codex 中安装。作者本次演示使用 5.6，思考程度为中等到极高，实际选项以账号为准。完整步骤与常见问题见 [使用教程](docs/USAGE.zh-CN.md)。

## 发行文件与源码

普通使用者直接下载上面的运行 ZIP 即可。绿色 **Code → Download ZIP** 和发行页的源码 ZIP 用于开发，不是日常使用的前置步骤。

| 制品 | 用途 | 内容 |
|---|---|---|
| [ChatGPT 运行 ZIP](https://github.com/frederick-kk/ppt-rebuilder-king/releases/download/v2026.10.25-quality.1/PPT-Rebuilder-King-ChatGPT-20261025.zip) | 整包上传 ChatGPT 项目「来源」 | 已认可原包的冻结副本，包含程序、依赖、SKILL、项目指令、两层清单与已脱敏历史资料 |
| [源码 ZIP](https://github.com/frederick-kk/ppt-rebuilder-king/releases/download/v2026.10.25-quality.1/PPT-Rebuilder-King-20261025-source.zip) | 阅读、开发、测试及开发重建 | 源码、锁文件、工程测试、构建工具和公开合成示例；不携带 `node_modules` 或历史诊断资料 |

源码包不作为项目运行来源。冻结运行包内的历史资料不参与默认执行；执行合同以 `compat-runtime/SKILL.md` 为准，项目设置使用同版本配套指令。每次输入使用新的独立作业目录，在同一目录中继续，保留暂停现场和已有回图。

主运行 ZIP 的 SHA256：`3a1ecd3a377206b654e4d7c5ed15dfd05292b8603e9ec5109634a20e8d1f82ed`。其包内原名为 `PPT Rebuilder King ChatGPT-20261025-组件与文字交付修复测试版.zip`；下载时无需改回该名称。[旧 Beta 1 发行版](https://github.com/frederick-kk/ppt-rebuilder-king/releases/tag/v2026.10.24-beta.1)保留供回退，不将旧 state 复制到新版本。

## 开发重建（开发者使用）

需要 Python 3.10+、Pillow、NumPy 和可用的 Node.js。源码开发环境须自行准备依赖；构建工具不会自动联网安装。以下命令仅生成独立的开发重建包，不用于重新制作或替换上方官方冻结运行 ZIP：

```text
python -m pip install -r compat-runtime/requirements.txt
npm ci --prefix compat-runtime
python tools/build_release.py --output ../quality1-dev-dist
python tools/verify_release.py "../quality1-dev-dist/PPT Rebuilder King ChatGPT-20261025-组件与文字交付修复测试版.zip"
python tools/verify_release.py "../quality1-dev-dist/PPT-Rebuilder-King-20261025-source.zip"
```

输出目录须为源码目录之外的新目录。开发构建使用锁文件和 393 份依赖文件白名单，生成运行包、源码包、校验值与配套指令；不携带历史资料。开发重建包与冻结主包的发行结构不同，不承诺两者 ZIP 字节一致。

`verify_release.py` 只支持开发重建格式，**不用于校验官方冻结原 ZIP**。冻结主包应核对上面的 SHA256，并验证其内置 `PACKAGE_MANIFEST.json` 和 `compat-runtime/MANIFEST.json` 的文件集合、大小及哈希；不为适配开发工具而重打包或修改冻结包。更多开发命令与合成 CLI 检查见 [工程说明](engineering/README.zh-CN.md)。

## 能力与限制

- 当前默认流程使用不透明背景、真实透明前景、真实透明组件板和独立透明主标题；不以洋红去底作为新作业默认路径。
- 每个任务请求一次调用、一张图片。意外多回图只有在明确为一次调用、完整保存原件并逐图核对时，才顺序采用第一份可用结果；这不授权追加调用。
- 宿主须实际查看回图并按任务角色登记。文件绑定与角色声明不能证明服务端请求、计费或所有细节还原正确；真正失败不会被强制改成完成。
- 复杂艺术字图片可以移动、缩放，其内部文字不能逐字编辑；表格由基础对象组合，公式仅支持有限结构。
- 字体未匹配时会明确估算并限制文字框适配。Microsoft YaHei 不随包提供；标尺在不同字体下的边缘标签仍有限制，见 [已知问题](KNOWN_ISSUES.md)。

**历史 Office 验收（2026-10-24，Beta 1）：** 当时在 Windows 的 Microsoft PowerPoint 16.0 中核对了指定缓存样本的对象与导出像素一致性，并对公开合成示例进行了编辑、保存副本及重新打开检查。这不是本轮质量版新增的 Office 验收，也不覆盖任意页面。Linux 的依赖、字体和显示结果仍需单独验证；不承诺跨字体完全一致或全平台编辑通过。详见 [发行验收范围](docs/RELEASE_VALIDATION.zh-CN.md)。

## 验证状态

2026-10-25：本轮候选的本地工程回归共 415 项，404 通过、11 明确跳过、0 失败/错误；该环境私下提供了两个真实历史源码对照文件，不能称为仅下载源码即可得到的独立回归结果。源码仓库的独立验收范围及跳过原因见 [发行验收范围](docs/RELEASE_VALIDATION.zh-CN.md)。

本地还用确定性夹具模拟缺字体，经真实 CLI 到达 `done` 并生成原生可编辑 PPTX：问题文字保留原文和原框，字号为 24.44px，继续标注为估算；该过程实际生图 0 次。它不是丢失的完整原作业重放、真实模型效果验证或新版 Office 验收。

公开 [合成示例](examples/synthetic-basic/README.md) 使用程序制作的 PNG 和模拟宿主声明，演练真实 CLI 与本地 PPTX 组装，不调用图像服务。它能说明技术结构与流程，不能充当真实截图还原、真人看图核对、提供方遥测或交付速度基准。计划任务数、实际报告调用数与端到端时间的区别见 [架构与验证边界](docs/ARCHITECTURE.zh-CN.md)。

本轮独立源码归档回归：**429 项，418 通过、11 明确跳过、0 失败/错误**；依赖为认可附件中逐项核验的 393 个固定文件，无私有诊断或旧兄弟包。此结果与认可候选的 415 项本地回归分开记录，详见 [验证范围](docs/RELEASE_VALIDATION.zh-CN.md)。

## 许可与贡献

第一方源码、工程工具和随包文档采用 [MIT License](LICENSE)，保留原版权行 `Copyright (c) 2026 FrederikKK / frederick-kk`。第三方依赖适用各自许可，见 [NOTICE](NOTICE.md)、[第三方许可](THIRD_PARTY_LICENSES.md) 和 [依赖清单](dependency/SBOM.json)。输入图片、外部字体和生成素材不因第一方 MIT 自动获得再分发授权。

贡献方式见 [CONTRIBUTING](CONTRIBUTING.md)，版本变化见 [CHANGELOG](CHANGELOG.md)。
