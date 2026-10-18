# 开发说明

入口为 `python -m newpath`；页面描述格式见 [references/scene.md](references/scene.md)。

```text
python -m newpath --work /absolute/job --source /absolute/slide.png
python -m newpath --work /absolute/job --input /absolute/result.json
python -m newpath --work /absolute/job
```

`newpath/` 是控制器、素材处理与原生输出实现；`tests/` 是合成输入和 PPTX 结构测试；`scripts/package.py` 用于白名单打包及清单验证。源码运行环境需要现有 Python 3.10+、Pillow、NumPy 和 Node 18+。仓库不提交展开的 `node_modules`，完整运行包已经包含对应依赖，可将其中的 `node_modules` 放在源码根目录用于开发。Python 依赖见 `requirements.txt`。

准备好运行环境后执行离线测试：

```text
python -m unittest discover -s tests -v
```

离线测试检查程序行为和文件结构，不替代真实图片效果或 PowerPoint 显示、编辑检查。
