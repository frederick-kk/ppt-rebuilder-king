# PPT Rebuilder King

将单页截图重建为对象可编辑的 PowerPoint：普通正文使用原生文本，复杂艺术字和插画使用独立图片。

**当前版本：2026.10.25-quality.1（组件与文字交付修复测试版）。仓库目前为 Private，公开前需由维护者明确决定。**

## 先下载这两个文件

- **[① 下载运行 ZIP](https://github.com/frederick-kk/ppt-rebuilder-king/releases/download/v2026.10.25-quality.1/PPT-Rebuilder-King-ChatGPT-20261025.zip)**
- **[② 查看 / 下载配套项目指令](https://github.com/frederick-kk/ppt-rebuilder-king/releases/download/v2026.10.25-quality.1/PPT-Rebuilder-King-20261025-PROJECT-INSTRUCTIONS.txt)**

**运行 ZIP 下载后保持压缩状态，直接整包上传到 ChatGPT 网页版项目的「来源」。不用解压或改名。** 项目指令是需要复制到项目设置中的文字。仓库保持 Private 时，仅有权限的账号可以下载；公开后普通读者才可访问。

也可以 [在线查看项目指令](docs/CHATGPT_PROJECT_INSTRUCTIONS.txt)，打开 Raw 后复制全部正文。配套指令补充了 GitHub 下载文件名与原运行包的对应关系，生成流程保持原样。

运行附件是已认可原 ZIP 的**字节复制**，只使用便于下载的 ASCII 文件名；不重新构建或替换包内文件。本轮改善组件提取和缺字体时普通空格的宽度估算，继续保留原文、原布局和字号最多缩小 25% 的限制。

## 在 ChatGPT 网页版使用

1. 点击上面的两个链接，下载运行 ZIP，并打开配套项目指令 TXT。
2. 在 ChatGPT 网页版新建项目，记忆设置选择「仅限本项目」。
3. 将运行 ZIP 整包上传到项目「来源」，把配套指令全文粘贴到「项目设置」→「项目指令」并保存。
4. 在项目内新开对话，选择具备文件处理、代码执行和生图能力的模型，附上一张单页高清图，同时发送：

   > 请读取本项目指定的 ZIP，按项目指令和包内工作流，将这张截图还原成可编辑 PPTX。

5. 等待 PPTX 文件，下载后用 WPS 或 PowerPoint 打开并检查。

这是 **ChatGPT 网页版项目**的使用流程，无需在本地 Codex 中安装。完整使用步骤和下载说明见 [使用教程](docs/USAGE.zh-CN.md)。作者本次演示使用 5.6，思考程度为中等到极高；实际模型、工具和选项以账号为准。

绿色 **Code → Download ZIP** 下载仓库源码。日常使用直接点击上面的「① 下载运行 ZIP」即可。发行附件列表见 [本版下载页](https://github.com/frederick-kk/ppt-rebuilder-king/releases/tag/v2026.10.25-quality.1)；开发和构建见 [中文开发说明](README.zh-CN.md)。GitHub 仅提供当前版本下载，新旧作业不要混用。

当前仍为测试发行版，结果受输入、宿主图像工具、字体与运行环境影响；请依据 [验收范围](docs/RELEASE_VALIDATION.zh-CN.md)和 [已知问题](KNOWN_ISSUES.md)评估结果，不将本地测试或个人使用反馈视为所有页面的效果或耗时保证。

## 参与与报告

[贡献指南](CONTRIBUTING.md) · [行为准则](CODE_OF_CONDUCT.md) · [使用支持](SUPPORT.md) · [安全报告](SECURITY.md)

提交问题前请删除个人信息、密钥、绝对路径及未经授权的图片。安全漏洞请先阅读安全报告流程，勿在普通 Issue 中披露细节。

## 许可

第一方代码和文档采用 [MIT](LICENSE)。第三方依赖和外部素材的许可范围见 [NOTICE](NOTICE.md) 与 [第三方许可](THIRD_PARTY_LICENSES.md)。
