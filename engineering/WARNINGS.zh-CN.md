# 作业检查器的证据边界

只在明确需要诊断时调用，不会由 `newpath` 自动导入。仅使用 Python 标准库，读取现有记录，不运行控制器、提取图像、生成图像或构建 PPT。

```powershell
python -B engineering/inspect_job.py --job "D:/existing-job" --output "D:/reports/job-inspection.md"
python -B engineering/inspect_job.py --job "D:/existing-job" --output "D:/reports/job-inspection.json" --format json
```

输出必须位于原始作业和 `compat-runtime` 之外，且不能已经存在。若作业目录不存在、检查期间文件变化或无法建立完整哈希清单，则拒绝写出报告。检查前后记录 SHA256、大小与修改时间；不跟随符号链接或目录联接。

退出码 `0` 只说明诊断报告已经写出。报告中 `evidence_status: incomplete` 表示缺失或异常证据，`visual_acceptance: null` 表示本工具没有给出视觉验收结论。缺失数据写为“未记录”或 `null`，不当作检查通过。`done` 和 `quality` 沿用原记录，`quality: not_evaluated` 仍表示质量未评估。

| 原始警告 | 含义和需要核对的内容 |
| --- | --- |
| `source_matte_collision_unresolved` | 主体颜色与抠图底色冲突未解决，可能误删主体颜色。核对源图与提取素材。 |
| `touch_slot_edge` | 主体触及槽位边缘，有裁切风险；警告本身不证明已裁切。 |
| `safe_area_cross` | 主体越过安全区域，边距或裁切情况需要核对。 |
| `cross_slot` | 相邻方向存在连续像素，可能跨槽或与邻项融合。 |
| `ambiguous_member_omitted` | 槽位对应有歧义，该成员被省略；没有补造替代图。 |

这些是风险记录，检查器不会把它们统一改成失败，也不会根据警告缺席宣布无风险。`skipped` 保留原始省略原因。

文字测量中 `measured` 表示现有字体度量记录，`estimated` 表示估算。保留 `remaining_overflow_px` 的已知超出量，表格中的逐行文字同样列入。`native_math_unmeasured` 表示原生公式几何没有测量；它的 `literal_fallback` 只描述兼容文字宽度，不是公式几何宽度。

`native_counts`、`equations` 和 `validation` 沿用已有构建报告，不重新构建或计数。`xml_well_formed: true` 仅是 XML 结构记录；`office_render_checked: false` 明确表示没有做 Office 实际渲染验收。

`attempt` 是任务重开序号，`issued` 是下发标志，都不是实际调用次数。工具身份和调用次数仅沿用调用方上报；未记录保持未知，不由任务数推导服务调用总量。
