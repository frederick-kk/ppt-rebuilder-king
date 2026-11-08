# 在 ChatGPT 网页版使用

**下载运行 ZIP → 整包上传 ChatGPT 项目的「来源」→ 保存项目指令 → 发送单页图片和提示词 → 下载 PPTX。**

普通使用者不需要安装本地 Codex，也不需要在电脑上解压运行 ZIP 或安装开发依赖。以下步骤对应 `2026.11.08-flow.r1`。下载本版运行 ZIP、配套项目指令和还原提示词。

## ① 从 GitHub 下载运行 ZIP

下载以下文件。

- **[下载 ChatGPT 运行 ZIP](https://github.com/frederick-kk/ppt-rebuilder-king/releases/download/v2026.11.05-package.1/PPT-Rebuilder-King-ChatGPT-20261108-flow-r1.zip)**
- **[下载配套项目指令 TXT](https://github.com/frederick-kk/ppt-rebuilder-king/releases/download/v2026.11.05-package.1/PPT-Rebuilder-King-20261108-flow-r1-PROJECT-INSTRUCTIONS.txt)**

- **[下载还原提示词 TXT](https://github.com/frederick-kk/ppt-rebuilder-king/releases/download/v2026.11.05-package.1/PPT-Rebuilder-King-20261108-flow-r1-PROMPT.txt)**

项目指令也可[在线查看并复制全文](CHATGPT_PROJECT_INSTRUCTIONS.txt)。将项目指令全文保存到项目设置。提示词也可[在线查看](../PROMPT.txt)，与当前单页图片一起发送。

运行 ZIP 下载后的文件名是 `PPT-Rebuilder-King-ChatGPT-20261108-flow-r1.zip`。记住保存位置，**不要解压、不要改名，下一步直接把这一份 ZIP 上传到 ChatGPT。**

也可以打开 [本版 Releases 下载页](https://github.com/frederick-kk/ppt-rebuilder-king/releases/tag/v2026.11.05-package.1)，向下找到 **Assets（附件）** 并展开，下载上面对应的运行 ZIP、项目指令 TXT 和还原提示词 TXT。

这里需要的是已经打包完成的 **ChatGPT 运行 ZIP**。`Source code`、名字带 `source` 的源码 ZIP 和绿色 **Code → Download ZIP** 用于源码开发，普通使用无需下载，也不需要先解压其他外层交付包。

## ② 在 ChatGPT 网页版新建项目

打开 ChatGPT 网页版，点击「项目」→「新建项目」，在创建时将记忆设置选择为「仅限本项目」，以账号实际界面提供的选项为准。

建好后，确认自己就在这个项目里。后面的 ZIP、项目指令和对话，都放在同一个项目下。一个项目先使用这一版运行包，避免混入旧版 ZIP 或旧作业文件。

## ③ 上传 ZIP，设置项目指令

1. 在项目「来源」上传刚下载的 `PPT-Rebuilder-King-ChatGPT-20261108-flow-r1.zip`，保持压缩状态。
2. 打开配套的 `PPT-Rebuilder-King-20261108-flow-r1-PROJECT-INSTRUCTIONS.txt`，复制全文。
3. 打开项目右上角的「项目设置」，将全文粘贴到「项目指令」并保存。

确认来源里能看见 ZIP，项目指令也已经保存，再往下发图片。不要只上传 ZIP 而漏掉项目指令。

## ④ 选择具备所需工具的模型

选择能够读取项目文件、执行代码和参考图生图的模型，实际模型、思考选项与额度以自己的账号为准。运行 ZIP 不提供模型账户、额度或外部服务凭据。

## ⑤ 单页高清图片和提示词一起发送

在这个项目内新开对话，上传**一张单页高清图**，同时发送下面这句：

> 请读取本项目来源中的 PPT-Rebuilder-King-ChatGPT-20261108-flow-r1.zip，按项目指令和包内工作流，将这张截图还原成可编辑 PPTX。

每次先处理一页。如果手里是六格缩略图，应先准备各页高清原图，再分别还原。

等待它提供 PPTX 文件，下载到电脑，用 WPS 或 PowerPoint 打开，检查文字编辑、图片移动和页面显示。普通文字与支持的基础图形为原生对象；复杂艺术字、纸卡和插画为独立图片，可移动、缩放，不等于其中每个字都可逐字编辑。

正常流程会由 ChatGPT 读取并解压项目中的运行 ZIP，按包内控制器连续执行。首次任务每项请求一张图片。真实输出失败时，控制器可下发一次受限修复，全作业最多追加一次调用。完成后提供 PPTX 与实际缺项、警告。若遇到真正失败，保留当前对话和现场，再根据明确说明处理，不以反复点击或重复发送图片来自动重画。

## 输出与已知边界

图片里的文字、嵌入照片内容和背景场景不等于逐字或逐物体可编辑。表格为对象组合，公式存在支持范围。

文件写出与 `done` 表示技术交付，不保证整体还原、字体显示、提供方调用或十分钟时效。需要视觉或编辑验收时，应另外打开 PPTX 检查。已知边界见 [KNOWN_ISSUES](../KNOWN_ISSUES.md)。

<details>
<summary>开发者与排障说明：内部工作流、回图登记和源码入口</summary>

运行环境需要已有 Python、Pillow、NumPy、Node.js，以及能够接收参考图并生成真实 alpha PNG 的图像工具。运行包携带 JavaScript 依赖，不提供字体。

宿主解压并读取 `compat-runtime/SKILL.md`，从程序目录之外的新作业目录启动，后续继续同一目录。运行来源只有当前包内 SKILL 与项目指令，开发文档和公开材料索引不参与默认生成流程。

### 当前流程

当前图片 → 工作图与辅助尺 → 背景和透明前景 → 一次页面登记 → 独立标题与透明组件板 → 原生文字、基础对象和图片组装 → PPTX。

背景保留自然融合的场景主体；独立照片、截图、扫描件与拼贴图按完整矩形前景保留，图内文字和人物保留在图中；独立纸卡、标签、图标和插画也属于前景；普通正文与简单几何使用原生对象。照片任务仍需照片，不能把标题图片登记进照片框。真实透明是 PNG alpha，不接受绘制的棋盘格或白色矩形冒充透明。

`generate` 是准备卡。宿主读取对应 `prompt_file` 的完整原文，按 `begin_template` 登记拟提交请求；只有首次 `invoke_once` 许可该任务调用一次。执行时保留指定参考图、尺寸、透明要求和单张请求，不将不同任务都改成同一段提示词。

回图先保存，再按实际像素填写 `role_review`、`output_sha256` 和本次 `request_sha256`。核对背景、前景、标题或组件成员是否匹配，并如实记录光晕、装饰或画质偏差。角色核对是宿主声明，不是程序自动语义识别或完整视觉验收；不从 caption 复制看图结论。

### 等待、意外多回图与真正失败

- `await_results` 只等待或登记已经存在的结果。`registration_required` 表示需补齐真实观察和字段，继续登记同一文件。路径、尺寸或登记字段出错时纠正登记。
- 明确一次实际调用意外返回多张时，保存完整原件，按真实顺序提交 `returned_outputs` 和每图独立核对。程序只采用第一份技术、来源和角色可用的已有结果，不做美观择优，不额外调用，也不为采用已有结果反复索要确认。
- 新作业按 `bounded-recovery/1` 执行。真实内容或技术输出失败时，只执行控制器明确下发的新 attempt 与修复提示，全作业最多追加一次调用。已有合格结果继续复用，预算用尽、绑定冲突、调用数无法核实、超调用或用户停止时保留现场并报告。
- 用户全局停止新增生成后，采用已有某项结果不会自动解除停止。正在调用的结果可以保存；剩余首次调用须按真实后续授权恢复。

用户要求诊断时，使用实际作业目录并显式指定 `--diagnostics` 输出。目标父目录需已存在，文件系统需支持原子硬链接发布。诊断索引记录所选作业的原图哈希、真实任务状态与缺失证据。若提示没有记录图像调用，先核对所选作业路径。

### 源码与运行包

源码 ZIP 用于开发。ChatGPT 运行 ZIP 包含运行程序、依赖、工作流、项目指令与配套工程资料。正常制作按本轮上传原图和包内工作流执行。构建与校验说明见 [中文开发说明](../README.zh-CN.md#构建与校验开发者使用)。

运行包根目录为 `PPT Rebuilder King-20261108-flow-r1`，程序身份为 `PPT Rebuilder King-20261108-flow-r1`，供 `--expected-package` 和首次 CLI 回传核对。下载文件按本版 SHA256 和两层文件清单核验。

最终运行 ZIP 解压后，程序入口为 `compat-runtime` 内的 `python -m newpath`；启动参数及本版身份以其中 SKILL 为准。作业目录必须在程序目录外。GitHub 仅提供当前版本。正在运行的作业不强制迁移，不复制旧 state 到新版本；维护者如需本地回退，仅让新作业使用已保留的本地旧包。

</details>
