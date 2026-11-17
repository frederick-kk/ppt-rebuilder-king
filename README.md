# PPT Rebuilder King

将单页截图重建为对象可编辑的PowerPoint。普通正文和基础图形使用原生对象，复杂艺术字、图标和内容图片使用独立图片。

当前发行版本为 **R28（2026.11.17-r28）**。

## 下载与使用

- [下载ChatGPT运行ZIP](https://github.com/frederick-kk/ppt-rebuilder-king/releases/download/v2026.11.17-r28/PPT-Rebuilder-King-ChatGPT-20261117-controller-r28.zip)
- [下载配套项目指令](https://github.com/frederick-kk/ppt-rebuilder-king/releases/download/v2026.11.17-r28/PPT-Rebuilder-King-20261117-r28-PROJECT-INSTRUCTIONS.txt)
- [下载还原提示词](https://github.com/frederick-kk/ppt-rebuilder-king/releases/download/v2026.11.17-r28/PPT-Rebuilder-King-20261117-r28-PROMPT.txt)

1. 在ChatGPT网页版新建项目，记忆选择仅限本项目。
2. 将运行ZIP整包上传到项目“来源”。
3. 将配套项目指令全文粘贴到项目设置并保存，也可[在线查看](docs/CHATGPT_PROJECT_INSTRUCTIONS.txt)。
4. 在项目内新开对话，选择具备文件处理、代码执行与参考图生图能力的模型，上传一张单页高清图，并发送下面的提示词。

```text
请读取本项目来源中的 PPT-Rebuilder-King-ChatGPT-20261117-controller-r28.zip，按项目指令和包内工作流，将这张截图还原成可编辑 PPTX。
```

5. 下载返回的PPTX，用PowerPoint或WPS检查页面和编辑效果。

运行ZIP保持压缩格式上传。绿色Code按钮中的Download ZIP提供仓库源码。完整步骤见[使用教程](docs/USAGE.zh-CN.md)。

## 制作顺序

背景 → 整页前景参考 → 一次页面描述 → 组件板 → 裁切与原生组装 → PPTX。

整页前景按任务指定安全底色生成，程序去底得到透明参考。最终组件板直接生成真实透明PNG。固定装饰容器与连续场景保留在背景，正文和基础结构原生制作。

新作业每个任务最多自动修复一次，全作业最多两次。已有合格回图继续使用，补登记与等待不消耗修复次数。

## 检查范围

还原质量和耗时受图片、模型、字体与运行环境影响。当前职业规划样本已交付并经过PowerPoint只读检查，网页计时11分14秒，仍有卡片背景和标题描边差异。验证范围见[验收说明](https://github.com/frederick-kk/ppt-rebuilder-king/blob/main/docs/RELEASE_VALIDATION.zh-CN.md)和[已知问题](KNOWN_ISSUES.md)。

[发行附件](https://github.com/frederick-kk/ppt-rebuilder-king/releases/tag/v2026.11.17-r28) · [文件校验值](https://github.com/frederick-kk/ppt-rebuilder-king/releases/download/v2026.11.17-r28/PPT-Rebuilder-King-20261117-r28-SHA256SUMS.txt) · [源码说明](README.zh-CN.md)

## 许可与反馈

第一方代码和文档采用[MIT](LICENSE)。第三方依赖保留各自的版权与许可，见[NOTICE](NOTICE.md)和[第三方许可](THIRD_PARTY_LICENSES.md)。

[贡献指南](CONTRIBUTING.md) · [使用支持](SUPPORT.md) · [安全报告](SECURITY.md)
