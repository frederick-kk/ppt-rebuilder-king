# 在ChatGPT网页版使用

## 下载文件

- [运行ZIP](https://github.com/frederick-kk/ppt-rebuilder-king/releases/download/v2026.11.17-r28/PPT-Rebuilder-King-ChatGPT-20261117-controller-r28.zip)
- [项目指令TXT](https://github.com/frederick-kk/ppt-rebuilder-king/releases/download/v2026.11.17-r28/PPT-Rebuilder-King-20261117-r28-PROJECT-INSTRUCTIONS.txt)
- [还原提示词TXT](https://github.com/frederick-kk/ppt-rebuilder-king/releases/download/v2026.11.17-r28/PPT-Rebuilder-King-20261117-r28-PROMPT.txt)

将运行ZIP保持压缩状态。项目指令也可[在线查看并复制](CHATGPT_PROJECT_INSTRUCTIONS.txt)。

## 创建与设置项目

1. 在ChatGPT网页版新建项目，记忆选择仅限本项目。
2. 将 `PPT-Rebuilder-King-ChatGPT-20261117-controller-r28.zip` 上传到项目“来源”，等待上传完成。
3. 打开项目设置，将项目指令TXT全文粘贴到项目指令并保存。
4. 在项目内开始新对话，选择具备文件处理、代码执行和参考图生图能力的模型。模型、思考选项与额度以账号界面为准。

## 上传图片并发送

每次上传一张单页高清原图，同时发送下面的提示词。

```text
请读取本项目来源中的 PPT-Rebuilder-King-ChatGPT-20261117-controller-r28.zip，按项目指令和包内工作流，将这张截图还原成可编辑 PPTX。
```

工作流先生成背景，再生成整页前景参考。两层完成后一次描述页面，随后制作透明组件板、切分并组装PPTX。普通文字和基础对象原生制作，复杂艺术字和图片作为独立图片保留。

下载PPTX，用PowerPoint或WPS查看文字、图片和布局。艺术字图片可独立移动缩放，内部字符保持图片内容。

## 继续当前作业

图片已生成但登记不完整时，继续补齐同一回图的信息。新作业每个任务最多自动修复一次，全作业最多两次，等待和登记修正不消耗修复次数。真正缺件、角色错误、来源冲突或修复仍失败时会保留现场及原因。

需要诊断时，在原对话明确要求导出该作业的诊断ZIP。诊断依赖原始作业记录仍可访问，环境切换后的可见文件应实际核对。

当前用时和质量边界见[验收说明](https://github.com/frederick-kk/ppt-rebuilder-king/blob/main/docs/RELEASE_VALIDATION.zh-CN.md)与[已知问题](../KNOWN_ISSUES.md)。源码构建入口见[源码说明](../README.zh-CN.md)。
