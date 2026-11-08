# PPT Rebuilder King · 2026.11.08-flow.r1

将单页截图重建为对象可编辑的 PowerPoint：普通正文和支持的基础图形使用原生对象，艺术字、图标、纸卡及复杂插画使用独立图片。工作空间为 1920×1080，辅助尺为 25/50/5；辅助尺用于定位，不让对象吸附网格。

本版提供完整 ChatGPT 运行附件，包含运行程序、依赖、工作流、项目指令和配套工程资料。下载后按本版 SHA256 与两层文件清单核对。

## 下载后直接上传 ChatGPT

- **[① 下载运行 ZIP](https://github.com/frederick-kk/ppt-rebuilder-king/releases/download/v2026.11.05-package.1/PPT-Rebuilder-King-ChatGPT-20261108-flow-r1.zip)**
- **[② 查看 / 下载配套项目指令](https://github.com/frederick-kk/ppt-rebuilder-king/releases/download/v2026.11.05-package.1/PPT-Rebuilder-King-20261108-flow-r1-PROJECT-INSTRUCTIONS.txt)**
- **[③ 下载还原提示词](https://github.com/frederick-kk/ppt-rebuilder-king/releases/download/v2026.11.05-package.1/PPT-Rebuilder-King-20261108-flow-r1-PROMPT.txt)**

运行 ZIP 下载后 **不用解压或改名，直接整包上传到 ChatGPT 网页版项目的「来源」**。配套指令也可 [在线查看](docs/CHATGPT_PROJECT_INSTRUCTIONS.txt)，打开 Raw 后复制全文。

1. 下载运行 ZIP、配套项目指令 TXT 和还原提示词 TXT。
2. 在 ChatGPT 网页版新建项目，记忆设置选择「仅限本项目」。
3. 上传运行 ZIP 到项目「来源」，将配套指令全文粘贴到「项目设置」→「项目指令」并保存。
4. 在项目内新开对话，选择具备文件处理、代码执行和生图能力的模型，上传一张单页高清图，并发送：

   > 请读取本项目来源中的 PPT-Rebuilder-King-ChatGPT-20261108-flow-r1.zip，按项目指令和包内工作流，将这张截图还原成可编辑 PPTX。

5. 下载返回的 PPTX，用 WPS 或 PowerPoint 打开并检查。

这是 ChatGPT 网页版项目的使用方式，无需在本地 Codex 中安装。完整步骤与常见问题见 [使用教程](docs/USAGE.zh-CN.md)，实际模型和工具选项以账号为准。

## 发行文件与源码

普通使用者下载运行 ZIP、项目指令和还原提示词。绿色 **Code → Download ZIP** 和源码 ZIP 用于开发。

| 制品 | 用途 | 内容 |
|---|---|---|
| [ChatGPT 运行 ZIP](https://github.com/frederick-kk/ppt-rebuilder-king/releases/download/v2026.11.05-package.1/PPT-Rebuilder-King-ChatGPT-20261108-flow-r1.zip) | 整包上传 ChatGPT 项目「来源」 | 运行程序、依赖、SKILL、项目指令、校验清单和配套工程资料 |
| [项目指令 TXT](https://github.com/frederick-kk/ppt-rebuilder-king/releases/download/v2026.11.05-package.1/PPT-Rebuilder-King-20261108-flow-r1-PROJECT-INSTRUCTIONS.txt) | 复制到项目设置 | 同版本项目运行指令 |
| [还原提示词 TXT](https://github.com/frederick-kk/ppt-rebuilder-king/releases/download/v2026.11.05-package.1/PPT-Rebuilder-King-20261108-flow-r1-PROMPT.txt) | 与单页图片一同发送 | 引用当前运行 ZIP 的单次还原请求 |
| [源码 ZIP](https://github.com/frederick-kk/ppt-rebuilder-king/releases/download/v2026.11.05-package.1/PPT-Rebuilder-King-20261108-flow-r1-source.zip) | 阅读、开发、测试及构建 | 源码、锁文件、工程测试、构建工具和公开合成示例；不携带 `node_modules` 或私有诊断资料 |
| [SHA256SUMS](https://github.com/frederick-kk/ppt-rebuilder-king/releases/download/v2026.11.05-package.1/PPT-Rebuilder-King-20261108-flow-r1-SHA256SUMS.txt) | 校验下载文件 | 本版附件的 SHA256 |

运行包解压根目录为 `PPT Rebuilder King-20261108-flow-r1`，执行入口是 `compat-runtime`。CLI 的 `--expected-package` 和 `runtime_identity.package_id` 使用 `PPT Rebuilder King-20261108-flow-r1`，发行版本为 `2026.11.08-flow.r1`。每次输入使用程序目录之外的新作业目录，后续继续同一目录并保留已有回图。

## 构建与校验（开发者使用）

使用 `tools/repack_runtime.py` 读取已审定完整归档，在源码目录外指定新的输出目录。运行 ZIP 保留该归档字节，源码 ZIP 独立构建。

```text
python tools/repack_runtime.py build --baseline ../downloads/PPT-Rebuilder-King-ChatGPT-20261108-flow-r1.zip --source . --output ../package-dist
python tools/repack_runtime.py verify --baseline ../downloads/PPT-Rebuilder-King-ChatGPT-20261108-flow-r1.zip --runtime ../package-dist/PPT-Rebuilder-King-ChatGPT-20261108-flow-r1.zip --source-archive ../package-dist/PPT-Rebuilder-King-20261108-flow-r1-source.zip
```

先下载本版运行 ZIP 作为基线，示例放在源码目录外的 `../downloads/`。构建工具核对 784 个归档成员、固定的内部运行清单、快照和全部 425 个运行文件。运行 ZIP 按已审定归档逐字节复制，源码 ZIP 独立构建，配套指令和提示词保持原始字节。Python、Node.js 和源码开发所需依赖另行准备。运行清单 SHA256 为 `eac29a95575b276a90499a1b9918eba39f274c7acfe81071c3ef9cdf6e63a16f`，运行快照 SHA256 为 `7423a29d88ea651f05c25f892bfc858d42b72c64da4850dfec3681b35ea56f16`。

运行包和源码包使用各自文件清单。下载后可核对公布的 SHA256，运行包还可核对 `RELEASE.json` 中记录的归档、运行清单与源码快照指纹。

## 能力与限制

- 默认流程使用不透明背景、真实透明前景、真实透明组件板和独立透明主标题。
- 每项首次任务请求一次调用、一张图片。意外多回图只有在明确为一次调用、完整保存原件并逐图核对时，才顺序采用第一份可用结果；这不授权追加调用。
- 新作业采用 `bounded-recovery/1`，全作业最多追加一次由控制器下发的修复调用。缺登记字段先补真实回执，沿用同一文件；修复预算用尽或用户停止时保留现场并报告。
- 宿主须实际查看回图并按任务角色登记。文件绑定与角色声明不能证明服务端请求、计费或所有细节还原正确；真正失败不会被强制改成完成。
- 复杂艺术字图片可以移动、缩放，其内部文字不能逐字编辑；表格由基础对象组合，公式仅支持有限结构。
- 字体未匹配时会明确估算并限制文字框适配。Microsoft YaHei 不随包提供；标尺在不同字体下的边缘标签仍有限制，见 [已知问题](KNOWN_ISSUES.md)。

图像服务、还原质量、端到端速度、Office 与跨平台编辑效果需要各自的实际验收。2026-10-25 的工程回归和 2026-10-24 的 PowerPoint 检查属于历史验证，范围见 [发行验收说明](https://github.com/frederick-kk/ppt-rebuilder-king/blob/main/docs/RELEASE_VALIDATION.zh-CN.md)。公开 [合成示例](examples/synthetic-basic/README.md) 使用程序制作的 PNG 和模拟宿主声明，演练真实 CLI 与本地 PPTX 组装，不调用图像服务。

## 许可与贡献

第一方源码、工程工具和随包文档采用 [MIT License](LICENSE)，保留原版权行 `Copyright (c) 2026 FrederikKK / frederick-kk`。第三方依赖适用各自许可，见 [NOTICE](NOTICE.md)、[第三方许可](THIRD_PARTY_LICENSES.md) 和 [依赖清单](dependency/SBOM.json)。输入图片、外部字体和生成素材不因第一方 MIT 自动获得再分发授权。

贡献方式见 [CONTRIBUTING](CONTRIBUTING.md)，版本变化见 [CHANGELOG](CHANGELOG.md)。
