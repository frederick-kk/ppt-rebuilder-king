# 一次页面登记

仅在控制器返回 `describe` 时读取。将完整 JSON 保存为文件，再用 `--input` 提交。原图事实字段的坐标相对于程序的 **1920×1080 工作原图**。foreground_box使用本次实际生成前景经程序归一后的1920×1080画布，独立读取。按结构、文字、复杂素材的顺序查看；每条文字自己的纵坐标独立读取，只有真实共有的锚点才能共用。

外层为 `{"kind":"layout","layout":{"items":[...]}}`。`items` 按阅读顺序。每项需要 `kind` 和对应字段；内部编号可省略，由程序稳定填写。`box` 为 `[左,上,宽,高]`，可填写 `z` 控制层级。默认容器在下、图片居中、文字在上。

原图观察可写`source_bounds:[左,上,右,下]`，程序换算box，适用于原本使用box的文字、图形、表格、公式及素材。直线继续使用points。组合素材`kind:"art"`可填写`source_parts`，每项用`bounds:[左,上,右,下]`记录一个真实部分，`name`可填写文字主体、圆弧刷痕或徽标等名称。程序取所有部分的并集作为唯一素材box，仍输出一张独立图片。新字段按source_parts、source_bounds、box的顺序确定源范围，target保留独立摆放含义。按原位制作时省略target。


## 实际前景裁片范围

foreground_component_policy为generated-foreground-members/1时，describe的第三张图是实际生成前景。每个art填写foreground_box:[左,上,宽,高]，量取该前景内完整主体，包含断开笔画，排除相邻文字和背景空纸底。程序只从此框提取参考像素，等比装入组件板。源图source_bounds/source_parts/box与PPT的target保持独立，原图仍决定最终位置及真实字样。标题与其他art统一走组件板，primary_wordart_id为null。

例如原图主体范围在[100,100,180,80]，生成前景实际偏移到[118,94,186,86]，可写box:[100,100,180,80]、foreground_box:[118,94,186,86]；PPT仍放回原图位置。照片保留完整矩形及图内文字，图标自身真实底座保留，周围固定纸底属于背景。

## 原图分层清单

source_layout_policy为source-layout-first/1时，layout同时填写background_regions列表。每项优先给出source_bounds:[左,上,右,下]及description，程序计算box；id可以省略，由程序填写。已有box使用[左,上,宽,高]，宽等于右减左，高等于下减上。先沿尺图读取各容器四边，再检查与整页1920×1080的相对位置。这里登记与页边、拱窗、飘带或山水连续构图的摄影、插画场景及其中主体，也登记按当前策略随背景保留的装饰容器。嵌套面板在description写清外框及内部空卡框的数量和排列，保留各框原位及可写空间。独立内容图片及艺术字写入items。空背景的background_regions使用空列表。

清单提供背景与前景的共同分配依据，页面内容仍按items的原图坐标组装。装饰纸底和金框按当前容器策略分配。该字段记录来源区域，不额外生成幻灯片对象。

describe给出的reading_images是工作原图的真实局部，working_box记录其工作图四边，zoom记录展示放大倍数。局部读到的点先除zoom再加working_box左上原点。整组边界回完整工作原图与尺图核对，卡片内框位置与实际字迹左边分别读取，各行保留源图左右留白。

## 正文

每条可见行一项，不能在 `text` 中写换行。普通文字固定 Microsoft YaHei，字号 `size` 以工作图像素计；颜色为六位 RGB。可选 `bold`、`italic`、`underline`、`align`（left/center/right）、`tracking`（字间距像素）、`rotation`（角度）。实际竖排用 `vertical:true`，不要用旋转或插入换行代替。竖排的 `rotation` 必须为0。

```json
{"kind":"text","box":[120,160,700,56],"text":"运输方式的变化","size":42,"color":"202020"}
```

同一行混合样式用 `runs`，各段只填写差异；合并文本必须逐字等于 `text`（也可省略 `text` 由段合并）。

```json
{"kind":"text","box":[120,230,700,50],"size":36,"runs":[{"text":"今天："},{"text":"快速送达","bold":true,"color":"1264A0"}]}
```

共同字号或颜色可以放在 `layout.styles`，再让项目通过 `style` 引用已有名称。每类字号按原图字形高度和行距填写，示例字号用于说明字段。项目内填写的值覆盖共有值，各行实际位置分别保留。

## 复杂素材与艺术字

按当前动作的分层策略登记。自然融入画面的风景、人物、车辆、建筑和实体花枝留在背景。当动作带有 `decorative_background_policy: decorative-background-containers/1` 时，承载页面内容的空装饰纸底、复杂金框、卡片外框及角花云纹保留在背景，正文和可移动内容分别登记。独立照片、扫描页或拼贴图内的纸张与边框随完整内容图片保留。缺少该策略字段的旧作业，独立纸张和卡片沿用原前景规则。标题艺术字、笔刷标签、图标和可移动小插画登记为前景，普通正文、原生字体样式能表达的标题及基础图形登记为原生对象。含金属渐变、立体厚度、特殊描边或投影的字样登记为art，短字样及数字也保留复杂效果。同一标题的普通字体段和复杂效果段各自登记源范围，普通段保持原生文字，复杂段保持独立art及完整字样。

需要独立图片的前景使用 `kind:"art"`，`box` 标记工作原图中的完整素材范围。直接源参考策略从本次工作原图同坐标取材制作组件参考板，明确记录其原图身份与真实裁片来源。使用其它已保存策略时，程序沿该作业既有前景参考取材。组件任务同时附上不变的本轮工作原图，第一张板确定格位与尺寸，第二张原图用于核对材质、字样、轮廓和原有白边。原图决定文字、位置和边界。`description` 简述主体身份、轮廓与材质。艺术字的 `lettering` 填原图实有的完整文字及标点，无字素材省略该字段，`description` 写清分色、笔锋、飞白、原有描边、材质及横竖排。用layout根字段primary_wordart_id选择是否创建独立艺术字任务。短标签、局部强调及适合组件板排布的艺术字可填写null，全部art保留并在组件板中生成。长幅复杂艺术字需要独立像素预算时填写该art的id，此成员须有非空lettering且不是embedded_image。省略字段沿用按目标框面积选取最大有字艺术组的规则，同面积时按源坐标和编号稳定选择。选中的成员从普通组件板中剔除，按任务references中的真实源参考生成独立alpha透明PNG；其余art使用组件板，板底为真实透明。最终嵌入各项真实生成回图。主标题完整范围包含与文字一体的徽标、飘带或刷痕底牌。以上判断在当前一次页面登记中完成。

```json
{"kind":"art","source_parts":[{"name":"文字","bounds":[350,60,1360,160]},{"name":"自带刷痕","bounds":[310,35,430,190]}],"description":"深棕色横排毛笔长标题及自带刷痕，按原图保留墨迹、飞白、细笔锋和原有描边","lettering":"以工作原图中的真实标题为准","presentation":"cutout"}
```

示例文字仅说明字段，实际提交填写原图文字。`presentation` 为 `cutout`（轮廓素材，默认）或 `rectangle`（完整内容图片等矩形素材）。`target` 可选，默认为 `box`，决定 PPT 中的位置。被选为独立任务的艺术字按独立等比适配规则处理。其他组件沿用既有目标框适配规则，组件板排布与最终摆放位置分别记录。

先量完整素材的左、上、右、下四边，再由右减左、下减上得到宽高。标题自带的圆弧、刷痕和断开尾笔都属于这个范围，字号与字行高度各自表示文字本身。按原图摆放时省略`target`即可沿用完整`box`，独立调整位置时填写完整组的目标框。

文字与圆弧高度不同时，在source_parts分别填写两者实际四边，由程序取并集，保留脱开笔触。逐部分沿最远笔端回到尺图读取坐标，底部尾笔、飞白与断开短笔都计入对应部分。各部分都来自同一工作原图。单一外观使用source_bounds即可，已有box输入仍可使用。

source_detail记录裁片范围、源图身份和裁片身份。使用source-parts-title-reference/1策略时，references只提供一张按source_parts范围保留原RGBA的真实标题裁片，范围外全透明；未填写分部时保留member.box范围。分部内的纸纹及装饰按真实像素保留，图像工具依标题成员生成透明素材。其它source-first策略继续提供已冻结的真实局部及完整工作图两份参考。实际生成范围、字样和行数按该标题成员及当前references确定。

例如全部复杂字样随组件板生成时，外层可写为以下结构。items中的各艺术字仍各自具有id、lettering、description及源范围，最终保持独立图片。

```json
{"kind":"layout","layout":{"primary_wordart_id":null,"items":[...]}}
```

primary_wordart_id为null时，艺术字保留art身份并通过组件板生成。显式指定时只能填写已有且符合条件的art编号，无效编号或类型会要求修正本次页面描述。

## 基础图形和直线

图形 `kind:"shape"`；`shape` 支持 rect、roundRect、ellipse、triangle、chevron、leftArrow、rightArrow、upArrow、downArrow、leftRightArrow、upDownArrow。填写 `box`；可选 `fill`、`stroke`（颜色或null）、`stroke_width`（像素）、`dash`（solid/dash/dot）、`opacity`（0～1）和 `rotation`。

直线 `kind:"line"` 使用 `points:[x1,y1,x2,y2]`，另可写 `stroke`、`stroke_width`、`dash`、`start_arrow`、`end_arrow`。端点类型为 none、triangle、stealth、diamond 或 oval。不要把可编辑基础线条列为复杂图片。

## 规则表格

`kind:"table"` 加 `box` 和矩形二维字符串 `rows`；单元格可包含换行。可选 `column_widths`、`row_heights`，分别与表格宽、高加总一致；省略则均分。可选 `size`、`color`、`fill`、`gridcolor`、`grid_width`。输出由独立单元格底色、文字与网格线组合，可编辑；不支持合并单元格。表格的rows已经登记的文字须保持唯一。需要不同单元格文字和底色样式时，可全部使用原生shape和text组合，每条可见行只登记一次。

## 有限公式

`kind:"formula"` 加 `box`、`text`（单行）及可选 `size`、`color`。以 Cambria Math 制作 Office 公式并保留兼容文本层。支持上下标、分数、根式、范数、求和、乘积、积分、极限、常用函数、希腊字母和基础集合符号。例如 JSON 中 `"text":"\\frac{a_1}{b}+\\sqrt{x}"`。

不支持完整 LaTeX、矩阵、cases 或多行公式；遇到不支持的表达保留可编辑原文并记录警告，不另开生成任务。未在实际 PowerPoint 中检查的兼容性不能称为通过。


source-first启动按原图与目标尺寸的像素需求选择基础组件板方案，保留每个成员的预算记录。主标题按回图完整字迹的原生比例放入目标框，宽度由完整组的比例决定。已保存作业按其planning_policy续作。
