---
name: ppt-rebuilder-king-chatgpt
description: 按当前图片制作对象可编辑单页PPT，按任务身份执行和登记真实回图。
---

# PPT Rebuilder King

本版只处理用户本次指定图片；解压后使用本目录，不查找历史作业或全局Skill。需要已有Python、Pillow、NumPy、Node和能接收参考图、生成真实alpha的图像工具；能力缺失如实报告，不自动安装或编造输出。

## 从空目录启动

```text
python -m newpath --work /absolute/new-job --source /absolute/current-image.png --expected-package "PPT Rebuilder King-20261107-components-r1"
```

首次生图前核对CLI回传runtime_identity.package_id为本版身份；不匹配时读取本次新ZIP并启动正确目录。

作业放在compat-runtime之外。同一图片始终继续该目录，不新建目录绕过暂停；旧版作业不迁移。新CLI冻结本地版本、调用策略、提示词审计和reviewed-existing-output/1交付策略。恢复会核对清单与保存身份。

## 单次流程

1. `generate`仅是准备任务。逐项读取该任务的prompt_file原文，复制begin_template填实际工具名/参数；不得改写或压缩prompt，不得重用上一任务参数。提交后仅首次`invoke_once`许可该项一次调用，按其request真实传入有序参考图、尺寸、透明参数与一张结果要求。能取得真实工具参数才写tool_arguments，不能记录密钥、Authorization或Base64。
2. 每份回图先保存，再在同一回合实际查看，与**当前task的角色和成员**核对，填写程序模板role_review与真实PNG的output_sha256，并带回本次request_sha256。不得按“最近一张图”绑定，不拿caption当执行prompt，也不能复制prompt作为看图描述。看图只核对这次回图，不增加专门分析调用或PPT预览。
3. 背景必须是原构图背景、无前景标题/正文；前景必须是整页独立素材参考；wordart必须只有指定主标题；board必须包含列出的全部指定成员，普通正文清空。**照片任务要照片，不能拿标题去填照片框。** 核对是宿主看图声明，程序不具备语义识图/提供方认证能力，不等于整体还原或逐像素视觉验收；附带装饰、光晕、画质等偏差如实写入observed/notes。角色错误不可填matches_task:true。
4. 用invoke_once返回的result_template登记真实文件；缺文件或声明不符只纠正已有结果，不擅自再生图。背景/前景可并行，串行登记且先背景；先回前景先保存。每批最多三项，等待不是重试许可。
5. `describe`只查看当前working与ruler，一次提交布局、每行原生正文、基础图形、独立艺术素材。原图决定文字和坐标，不能用生成前景改写事实。艺术字填准确lettering与外观description；最大有字艺术组唯一走独立标题。工作1920×1080，尺25/50/5只辅助定位，不吸附格点。
6. `await_results`只等待或登记已存在回图；未开始项只按新generate准备。`done`立即给pptx下载，简述实有缺项和警告，然后停止。正常不制作诊断包、渲染预览或追加修饰调用。

```text
python -m newpath --work /absolute/new-job --input /absolute/result.json
python -m newpath --work /absolute/new-job
```

## 预算与异常

每任务最多一次调用，请求一张。背景只做一次，其他任务不能沿用背景/标题回图。调用中只等候；路径/尺寸差异/轮询waiting不能触发重画。prompt_file、参考板、工作原图不是生成输出，不能改名、复制或重编码后冒充结果。跨任务同输出文件像素或同服务output ID会被拒绝；这些检查不是服务端身份认证。

单次调用明确invocation_count=1、意外返回多张时，保存完整有序原件清单，按程序要求逐图独立核对role_review，通过returned_outputs原样登记。程序仅顺序采用第一份技术及角色可用结果，未查看/不合格者保留原因，不择优、不重画；不再为采用已有结果索要确认。这不授权额外调用。已发生额外调用、调用数未知而无法确证单次、真正失败、用户停止等仍保留现场，不能伪造done。

次数如实登记，未知省略/null，不用文件数推算调用数，不隐瞒结果。用户全局停止新增生成时保持stop，采用已有某任务不解除全局stop；只在用户后来明确允许剩余任务首次调用时，引用原话resume_unstarted。真正失败任务要再次生成，仍需明确授权，不自动重试或编造用户原话。paused/error保留全部文件并解释具体失败，不反复建议同一句确认绕过未完成任务。

调用前请求锁定；调用后实际prompt确实取得才写executed_prompt，不可得保留unknown。不同原文保存双方及SHA和警告，不把工具返回说明冒充调用prompt。prompt审计match/mismatch/unknown与**角色看图核对**分开：文字差异本身不耗图重画，错误主体不能只靠技术alpha检查或提示词警告放行。首次审计不可被后续省略洗掉，补充/冲突另存；done终态。

## 保留的素材及排版合同

背景保留自然融合的场景、人物、车、建筑与真实花枝；标题、纸张、标签、图标、独立小插画属于前景；正文与简单形状原生编辑。不透明背景transparent_background:false，其余前景/board/wordart均true；真实PNG alpha，不接受画出的棋盘/白底。组件板按原像素需求及有收益的第四板规则规划，每批三项不是总板数上限；不自行扩板或重排。

标题按alpha>=2核心等比放入目标框，保留所有原RGBA（含alpha1、hidden RGB和原白色描边），紧矩形裁切；完整图片框可因淡边超出原框，四页工程边界不变。仅当真实alpha、确有透明空白、存在完整内缩alpha>1核心且触边全部为alpha1时，技术放行并警告，无需重画。alpha>1触边、无透明空白、假透明仍拒绝，不用补白或去色伪修。

普通透明组件不按色抠像、不删连通块；有参考和各成员核心时按alpha>1识别几何，原RGBA全保留，先分列再分行。原宽隔带存在多条候选时，程序仅对有参考、未归属、最大alpha≤3、单团≤8像素且整板累计≤64像素的微小碎点作局部几何复核，保留原切线并记录weak_native_grid_speck_tolerance；裁片继续保留全部原RGBA。继续保留原窄隔带规则：唯一2–5px真空带、相邻主体二维参考归属明确且没有核心跨线时恢复并保存证据。无参考、复核后仍有歧义、真融合/切边仍拒绝，不强制把未知主体入稿。正确提取不代表模型没有附带装饰或光晕。

正文保留原文/字体/runs和原生编辑性，仅支持的水平普通行做有限宽度拟合，最大缩字号25%；缺匹配字体明确估算，ASCII数字估0.67em、普通ASCII空格估0.33em，其他沿用；实际匹配字体测量和25%字号下限保持。真实超预算不能假装已测量或任意缩小。文件写出和done是技术交付状态，不代表完整还原、真实计费次数或十分钟时效已验收。
