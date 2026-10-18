# PPT Rebuilder King · PPT 还原王

将一张幻灯片截图还原为可编辑的 PowerPoint：普通正文按可见行建立原生文本框，基础图形、线条、表格和有限公式保持可编辑，艺术字及复杂插画作为独立图片放置。

本项目把图像生成与本地组装连接起来。生成任务由宿主的图像工具完成，程序负责坐标、组件板、裁切去底、任务恢复和 PPTX 写出。

## 下载与使用

- [完整运行包](dist/新路径-ChatGPT-20261018-r1.zip)
- [配套项目指令](dist/新路径-项目指令.txt)

在具备代码执行和参考图片生成能力的聊天环境中，将完整运行包放入项目文件，粘贴配套项目指令，在新聊天中上传需要还原的截图。程序只处理当前明确指定的图片。

完整包包含运行源码和锁定的 JavaScript 依赖。宿主还需要现有 Python 3.10+、Pillow 10+、NumPy 1.24+ 和 Node；不需要安装全局 Skill。详细执行方式见 [SKILL.md](SKILL.md)。

## 制作流程

当前图片准备 → 背景与完整前景生成 → 一次页面登记 → 分批组件板生成 → 切分去底与原生组装 → PPTX 交付。

- 背景和整页前景分别生成，前景使用洋红底；组件板由程序选择合适的共同底色。
- 每项只请求一个结果，组件板每批最多三项。已有结果按任务、角色和尝试编号登记，重复登记不会再次生成。
- 采用首次技术可用结果；生成失败保存现场，只有用户明确要求才重新开放未完成任务。
- PPTX 完整写出后停止，不默认追加取证包、预览或自动返修。

## 源码开发

入口为 `python -m newpath`；页面描述格式见 [references/scene.md](references/scene.md)。

```text
python -m newpath --work /absolute/job --source /absolute/slide.png
python -m newpath --work /absolute/job --input /absolute/result.json
python -m newpath --work /absolute/job
```

`newpath/` 是控制器、素材处理与原生输出实现；`tests/` 是合成输入和 PPTX 结构测试；`scripts/package.py` 用于白名单打包及清单验证。仓库不提交展开的 `node_modules`，完整运行包已经包含对应依赖，可将其中的 `node_modules` 放在源码根目录用于开发。Python 依赖见 `requirements.txt`。

准备好运行环境后执行离线测试：

```text
python -m unittest discover -s tests -v
```

离线测试检查程序行为和文件结构，不替代真实图片效果或 PowerPoint 显示、编辑检查。

## 能力边界

艺术字及插画是可独立移动的图片；普通文字和支持的基础对象原生制作。公式支持有限表达，不是完整 LaTeX 排版系统。

生成素材可能发生内容偏差；固定去底和主体筛选可能损失细节，紧裁后填满目标区域可能改变部分比例。单个素材无法提取时会列出缺失，可能继续输出。实际效果和耗时需要结合输入及宿主工具判断，不承诺任意页面在固定时间内完成。

## 许可证

[MIT License](LICENSE)。第三方运行依赖的许可证随完整运行包保留。
