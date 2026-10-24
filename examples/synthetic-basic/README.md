# 完全合成的可编辑示例

`input.png` 由 tools/smoke_test.py 使用 Pillow 程序绘制；没有真实学生/客户截图、第三方图片或模型生成图片。`layout.json` 是对应 1920×1080 对象布局，只有相对内容与数值，没有机器路径。

`expected-output.pptx` 通过实际新 CLI 从空作业依次完成准备、begin、登记、describe 与 done。四个任务为背景、透明前景、独立透明艺术标题和一个普通艺术组件板；回图均是确定性程序夹具，角色核对为明确标注 Synthetic automated fixture 的模拟宿主声明，不是人工看图或提供方认证。调用前 request_sha256 使用真实许可记录；重复 begin 已验证不再给第二次许可。控制器的提供方调用数字段保持 null，不把四次本地许可申报成四次模型调用；本脚本实际没有调用图像提供方。

可以在 PowerPoint 中选择两行原生文字、一个圆角形状、一条线，并移动两张艺术图片。艺术标题与橙色徽章内部不是可逐字编辑的文字。软件检查确认这些原生/图片结构存在；2026-10-24 额外验证：本机 Microsoft PowerPoint 16.0 只读打开并查看公开原件；在另一个私有副本中修改原生文字、形状填色、线条及图片位置，保存后重新打开确认修改保留。公开原件 SHA 未变。该项只证明这个合成示例的相应对象可编辑，不说明真实图像模型效果、全平台显示或交付速度。

公开 PPTX 是私有作业成品的独立副本，只将图片 cNvPr@descr 中的绝对路径替换为合成说明，并将 core 文档作者、最后修改者及时间改为固定合成元数据。完整 ZIP member 检查确认图片字节、文本、几何及其它非目标内容未改变；原始私有 PPTX 保持原样。公开副本已扫描全部解压部件及 XML，未发现机器路径、密钥模式或外部关系。

复现（Python、Pillow、NumPy、Node 与运行依赖需按项目说明预先可用）：

    python tools/smoke_test.py --runtime /path/to/extracted/compat-runtime --work /new/empty/outside-directory

默认只在指定私有 work 下写作业与 public-example 四个公开文件。为发行目录生成可另加 --example-dir /new/empty/public-example；拒绝覆盖已有证据。工作目录、state、receipt、真实请求记录和绝对路径日志不随本示例公开。

这是合成的软件流程示例，不是一次真实模型图片还原案例。本例代码、布局及程序绘制素材按项目根 MIT 许可提供。
