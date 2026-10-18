---
name: ppt-rebuilder-king-chatgpt
description: 根据当前用户截图制作对象可编辑的单页 PowerPoint；使用外部图像工具生成素材，再由本包组装。
---

# PPT Rebuilder King

只处理用户这次明确指定的图片。解压后在本目录运行，不查找历史作业或全局 Skill。需要现有 Python、Pillow、NumPy、Node，以及可实际参考图片的图像生成工具；缺少能力时说明缺项并保留现场，不自动安装或编造产出。

先开始一个空目录作业：

```text
python -m newpath --work /absolute/job --source /absolute/current-image.png
```

按返回的 `kind` 连续完成：

- `generate`：逐个读取任务提示文件，以列出的图片为真实参考，每项请求一个结果。两张基础图可并发生成，先登记背景，再登记完整洋红前景；前景先返回就保存等待。组件板每批最多三张，可并发，回调串行。不要自行增加调用、变更底色或重排槽位。
- `describe`：查看返回的工作原图和坐标图，按 [页面登记格式](references/scene.md) 一次填写结构、逐行正文及独立素材。原图决定文字和位置。艺术字列为图片，不再叠加同文原生文字。禁止用生成前景判断页面事实。
- `await_results`：等待已经下发的任务，或登记已有返回文件。此状态不授权重新生成。需要继续时沿用同一目录。
- `paused` 或 `error`：停止新增调用，保留文件，简述原因。用户明确要求再生成时，才使用 `--retry-task`。
- `done`：立即提供 `pptx` 下载。说明已知缺失或警告，然后停止。不要制作预览、取证 ZIP、视觉复核或自动返修。

结果 JSON 使用程序给出的模板；`file` 填真实本地图片路径，任务、角色和尝试编号原样保留。可附实际已知的工具名称、调用标识和 `invocation_count`；未暴露的信息省略，不能用本地文件名冒充服务标识。

```text
python -m newpath --work /absolute/job --input /absolute/result.json
python -m newpath --work /absolute/job
```

背景、前景和每张组件板均采用首次技术可用结果，正常任务数为 2＋组件板数。程序负责裁切、去底、主体分离、原生文字和可编辑图形组装。整图损坏或无内容会停止；单个素材缺失、贴边或跨槽会记录警告，可能继续交付。文件成功写出不等于质量合格；不要声称已通过未做的 PowerPoint 检查或十分钟验收。
