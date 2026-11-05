# 第三方许可原文

`node/` 保存运行发行包所含 17 个 npm 包的原始许可文本。第一方源码及文档使用根 `LICENSE`；这里的上游声明独立保留，不被根 MIT 替换。

- 17 份原许可文件或 README 完整逐字节复制；isarray 的完整 MIT 原文在其 README。
- 另存 pako 原 `lib/zlib/adler32.js` 的 Zlib 许可注释块；注释、版权行和换行保持原样。
- jszip 保留其原始完整双许可文件。
- `https 1.0.0` 的上游 tarball 仅含声明 ISC 的 331 字节 `package/package.json`，未提供许可全文。运行附件保留该元数据文件；源码附件不携带 `node_modules`，锁文件保留上游依赖声明。

[SOURCE_INDEX.json](SOURCE_INDEX.json) 记录原版本、来源相对路径、对应官方 tarball、源文件/副本 SHA-256 与提取范围；完整依赖清单见 [SBOM.json](../dependency/SBOM.json)。来源/完整性核对是字节事实，不是权利或法律认证。
