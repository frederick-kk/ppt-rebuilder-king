# PPT Rebuilder King

将单页截图重建为对象可编辑的 PowerPoint：普通正文使用原生文本，复杂艺术字和插画使用独立图片。

**当前版本：2026.11.05-package.1。** 本次只整理发行包，精简普通用户下载的内容；程序、生成提示词和操作流程保持不变。

## 先下载这两个文件

- **[① 下载运行 ZIP](https://github.com/frederick-kk/ppt-rebuilder-king/releases/download/v2026.11.05-package.1/PPT-Rebuilder-King-ChatGPT-20261105.zip)**
- **[② 查看 / 下载配套项目指令](https://github.com/frederick-kk/ppt-rebuilder-king/releases/download/v2026.11.05-package.1/PPT-Rebuilder-King-20261105-PROJECT-INSTRUCTIONS.txt)**

**运行 ZIP 下载后保持压缩状态，直接整包上传到 ChatGPT 网页版项目的「来源」。不用解压或改名。** 项目指令是需要复制到项目设置中的文字，也可 [在线查看](docs/CHATGPT_PROJECT_INSTRUCTIONS.txt)，打开 Raw 后复制全文。

## 在 ChatGPT 网页版使用

1. 下载运行 ZIP，并打开配套项目指令 TXT。
2. 在 ChatGPT 网页版新建项目，记忆设置选择「仅限本项目」。
3. 将运行 ZIP 整包上传到项目「来源」，把配套指令全文粘贴到「项目设置」→「项目指令」并保存。
4. 在项目内新开对话，选择具备文件处理、代码执行和生图能力的模型，附上一张单页高清图，同时发送：

   > 请读取本项目指定的 ZIP，按项目指令和包内工作流，将这张截图还原成可编辑 PPTX。

5. 下载返回的 PPTX，用 WPS 或 PowerPoint 打开并检查。

这是 **ChatGPT 网页版项目**的使用流程，无需在本地 Codex 中安装。完整步骤见 [使用教程](docs/USAGE.zh-CN.md)，实际模型和工具选项以账号为准。

运行包只保留运行所需程序、依赖、工作流、项目指令、校验清单和必要许可说明。开发工具、测试与历史资料不放入普通运行包。绿色 **Code → Download ZIP** 下载仓库源码；开发者可下载 [源码 ZIP](https://github.com/frederick-kk/ppt-rebuilder-king/releases/download/v2026.11.05-package.1/PPT-Rebuilder-King-20261105-source.zip) 并阅读 [中文开发说明](README.zh-CN.md)。全部附件见 [本版下载页](https://github.com/frederick-kk/ppt-rebuilder-king/releases/tag/v2026.11.05-package.1)，文件校验值见 [SHA256SUMS](https://github.com/frederick-kk/ppt-rebuilder-king/releases/download/v2026.11.05-package.1/PPT-Rebuilder-King-20261105-SHA256SUMS.txt)。

结果受输入、宿主图像工具、字体与运行环境影响。本次发行整理没有新增还原质量、交付速度或 Office 编辑验收；已有证据范围见 [验收说明](https://github.com/frederick-kk/ppt-rebuilder-king/blob/main/docs/RELEASE_VALIDATION.zh-CN.md)和 [已知问题](KNOWN_ISSUES.md)。

## 参与与报告

[贡献指南](CONTRIBUTING.md) · [行为准则](CODE_OF_CONDUCT.md) · [使用支持](SUPPORT.md) · [安全报告](SECURITY.md)

提交问题前请删除个人信息、密钥、绝对路径及未经授权的图片。安全漏洞请先阅读安全报告流程，勿在普通 Issue 中披露细节。

## 许可

第一方代码和文档采用 [MIT](LICENSE)。第三方依赖和外部素材的许可范围见 [NOTICE](NOTICE.md) 与 [第三方许可](THIRD_PARTY_LICENSES.md)。
