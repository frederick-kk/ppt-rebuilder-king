---
name: ppt-rebuilder-king-chatgpt
description: 按当前截图生成图层与组件，组装文字及对象可编辑的单页PowerPoint。
---

# PPT Rebuilder King

使用本包的compat-runtime和用户本次指定的原图。作业放在程序目录外的空目录，需要已有Python、Pillow、NumPy、Node及能参考图片的图像工具。

## 启动

```text
python -m newpath --work /absolute/new-job --source /absolute/current-image.png --expected-package "PPT Rebuilder King-20261117-controller-r28" --sequential-layers --foreground-components --keyed-materials --compact --file-actions --action-file /absolute/next-action.json
```

按控制器当前动作连续完成同一作业。顺序为背景、整页前景、一次页面描述、组件制作与切分、PPTX组装。首次启动只派发背景，背景登记成功后派发前景，两层登记后返回describe。页面文字、字号和坐标的完整描述在此时一次提交。

## 图层归属

背景保留连续场景及其中人物、车辆、建筑和植物。空装饰纸底、金框、固定卡片外框与角花留在背景，保持纹理、位置和可写空间。

整页前景直接参考本次原图，按原位保留普通文字的参考外观、完整艺术字、标签及自带底纹、独立图标和内容图片。移除背景场景与固定空容器，空区按当前任务指定纯色生成，由程序转换为真实透明PNG。实际已有透明通道的回图按实测处理。独立照片、截图、扫描页和拼贴图保留完整矩形，其内部文字、人物和标志随图保留。

## 连续执行

1. generate提供本任务的提示词、参考文件和begin_template。读取当前完整模板，填写真实工具及实际参数后提交。收到该任务首次invoke_once后调用图像工具一次，请求一张。调用前以一句话写清当前图层或组件目标，随后执行工具。
2. 回图保存并实际查看，按result_template填写文件及role_review后登记。原始输出和真实失败保留。背景核对场景与空容器。整页前景是采样参考，核对主体完整、身份和裁片位置；指定主体外的固定容器残留先记录，主体可完整采样时继续。最终组件板逐格核对指定成员、真实字样和主体周围去底结果。指定纯色空区属于程序去底输入，未知服务字段据实保留unknown。
3. registration_required时补齐现有回图的观察与登记。使用返回模板中的文件绑定，提示文字差异按实际回图记录，已符合角色的现有结果继续登记。透明通道、边界和切分的技术结论按程序实测记录，实际字形、完整性与外观分别观察。
4. 两层完成后，describe查看工作原图与同源尺图，按schema_file一次填写结构、逐行正文、基础图形和独立素材。普通文字每条可见行一个原生文本框，混合样式用runs，基础形状和规则表格保持原生编辑。复杂艺术字保留图片身份，准确填写lettering和完整边界。内容图片用art.embedded_image:true保留图内全部内容。细字需要时查看对应真实局部。原图决定文字、源范围和最终摆放。对每个art另填foreground_box:[左,上,宽,高]，按describe提供的1920×1080实际前景量取完整主体，排除相邻标签和固定纸底。两组坐标各自读取，主标题的圆弧与断笔要完整包含。表格每条可见文字只登记一次，彩色单元格可用原生形状与文字制作。
5. 程序从已生成的整页前景裁出素材参考并装板。按当前任务生成真实透明PNG组件板，保留指定槽位、完整主体与实际透明边缘，随后由程序切分与透明紧裁。主标题与其他艺术字统一通过前景裁片组件板制作。登记工具原始回图，派生裁拼文件另记处理来源。逐成员核对主体完整、额外纸底及透明空区，保留照片内部真实底色。完整组件换格时可在同次观察中填写observed_cell_members，缺项或角色失败按真实结果记录。
6. 真实失败由控制器安排修复，每项任务至多一次，全作业至多两次。背景修复以首张待修背景为编辑目标，另一张原图提供事实和位置。多张已存在回图逐张复核，采用首个合格结果并如实保留实际数量。登记未完成时先补现有回图，沿同一作业继续。真实仍不合格时保留具体缺项。
7. done立即提供PPTX与简短实有缺项、警告，制作结束。完整回执保存在作业中。

每次提交后继续读取下一动作。完整动作位于action_file，begin_template_file与request_file提供实际模板和请求。state.json、任务编号、摘要和布局冻结由程序维护。

```text
python -m newpath --work /absolute/new-job --input /absolute/result.json --compact --file-actions --action-file /absolute/next-action.json
python -m newpath --work /absolute/new-job --compact --file-actions --action-file /absolute/next-action.json
```

沿用同一作业目录恢复，遵循其已冻结的流程。重复begin和等待状态只处理已有调用与回图。每次真实调用与输出如实记录，完整生成板留作素材处理依据，PPT中嵌入独立组件和原生对象。

## 按需诊断

用户要求时对指定现有作业导出一个ZIP，写到作业目录外，保留原有记录。

```text
python -m newpath --work /absolute/actual-job --diagnostics /absolute/本轮流程诊断.zip
```

文件写出表示技术交付。实际还原质量、PowerPoint检查及十分钟耗时按已完成的检查如实说明。
