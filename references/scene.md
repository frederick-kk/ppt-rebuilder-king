# 一次页面登记

仅在控制器返回 `describe` 时读取。将完整 JSON 保存为文件，再用 `--input` 提交。所有坐标均相对于程序的 **1920×1080 工作原图**，不是上传原图或生成前景的像素。按结构、文字、复杂素材的顺序查看；每条文字自己的纵坐标独立读取，只有真实共有的锚点才能共用。

外层为 `{"kind":"layout","layout":{"items":[...]}}`。`items` 按阅读顺序。每项需要 `kind` 和对应字段；内部编号可省略，由程序稳定填写。`box` 为 `[左,上,宽,高]`，可填写 `z` 控制层级。默认容器在下、图片居中、文字在上。

## 正文

每条可见行一项，不能在 `text` 中写换行。普通文字固定 Microsoft YaHei，字号 `size` 以工作图像素计；颜色为六位 RGB。可选 `bold`、`italic`、`underline`、`align`（left/center/right）、`tracking`（字间距像素）、`rotation`（角度）。实际竖排用 `vertical:true`，不要用旋转或插入换行代替。竖排的 `rotation` 必须为0。

```json
{"kind":"text","box":[120,160,700,56],"text":"运输方式的变化","size":42,"color":"202020"}
```

同一行混合样式用 `runs`，各段只填写差异；合并文本必须逐字等于 `text`（也可省略 `text` 由段合并）。

```json
{"kind":"text","box":[120,230,700,50],"size":36,"runs":[{"text":"今天："},{"text":"快速送达","bold":true,"color":"1264A0"}]}
```

共同字号或颜色可以放在 `layout.styles`，例如 `{"body":{"size":32,"color":"333333"}}`，再让项目写 `"style":"body"`；项目内填写的值覆盖共有值。不得用共享样式覆盖各行实际位置。

## 复杂素材与艺术字

使用 `kind:"art"`，`box` 标记工作原图中的独立素材范围。程序从**本轮生成的完整前景**同坐标裁出参考，不能换成原图裁片。`description` 简述主体身份、轮廓与材质。艺术字的 `lettering` 填完整文字以约束生成，勿再添加同文原生文本框。

```json
{"kind":"art","box":[310,35,1050,155],"description":"深棕色毛笔长标题，断开的墨迹和细笔锋","lettering":"以工作原图中的真实标题为准","presentation":"cutout"}
```

示例文字仅说明字段，实际提交必须填原图文字。`presentation` 为 `cutout`（轮廓素材，默认）或 `rectangle`（照片、纸张等矩形素材）。`target` 可选，默认为 `box`；它只决定 PPT 中的位置。不要按组件板排布修改 `target`。紧裁图片会直接填满目标四边，不自动居中保比例。

## 基础图形和直线

图形 `kind:"shape"`；`shape` 支持 rect、roundRect、ellipse、triangle、chevron、leftArrow、rightArrow、upArrow、downArrow、leftRightArrow、upDownArrow。填写 `box`；可选 `fill`、`stroke`（颜色或null）、`stroke_width`（像素）、`dash`（solid/dash/dot）、`opacity`（0～1）和 `rotation`。

直线 `kind:"line"` 使用 `points:[x1,y1,x2,y2]`，另可写 `stroke`、`stroke_width`、`dash`、`start_arrow`、`end_arrow`。端点类型为 none、triangle、stealth、diamond 或 oval。不要把可编辑基础线条列为复杂图片。

## 规则表格

`kind:"table"` 加 `box` 和矩形二维字符串 `rows`；单元格可包含换行。可选 `column_widths`、`row_heights`，分别与表格宽、高加总一致；省略则均分。可选 `size`、`color`、`fill`、`gridcolor`、`grid_width`。输出由独立单元格底色、文字与网格线组合，可编辑；不支持合并单元格。

## 有限公式

`kind:"formula"` 加 `box`、`text`（单行）及可选 `size`、`color`。以 Cambria Math 制作 Office 公式并保留兼容文本层。支持上下标、分数、根式、范数、求和、乘积、积分、极限、常用函数、希腊字母和基础集合符号。例如 JSON 中 `"text":"\\frac{a_1}{b}+\\sqrt{x}"`。

不支持完整 LaTeX、矩阵、cases 或多行公式；遇到不支持的表达保留可编辑原文并记录警告，不另开生成任务。未在实际 PowerPoint 中检查的兼容性不能称为通过。
