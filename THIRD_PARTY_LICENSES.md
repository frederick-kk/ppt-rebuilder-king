# 第三方依赖与许可

源码发行包保留源码与真实锁文件，不携带 `node_modules`；ChatGPT 运行附件保留 17 个实际依赖目录，另有 `https` 空壳元数据目录，运行文件逐项保持不变。第一方 MIT 不替换第三方许可。

| npm 包 | 锁定版本 | 上游声明 | 本包保留的原文 |
|---|---|---|---|
| `@types/node` | 22.20.5 | `MIT` | [@types-node-22.20.5-LICENSE](LICENSES/node/@types-node-22.20.5-LICENSE) |
| `core-util-is` | 1.0.3 | `MIT` | [core-util-is-1.0.3-LICENSE](LICENSES/node/core-util-is-1.0.3-LICENSE) |
| `https` | 1.0.0 | `ISC` | 运行附件保留原331字节元数据；上游未提供全文 |
| `image-size` | 2.0.4 | `MIT` | [image-size-2.0.4-LICENSE](LICENSES/node/image-size-2.0.4-LICENSE) |
| `immediate` | 3.0.6 | `MIT` | [immediate-3.0.6-LICENSE.txt](LICENSES/node/immediate-3.0.6-LICENSE.txt) |
| `inherits` | 2.0.4 | `ISC` | [inherits-2.0.4-LICENSE](LICENSES/node/inherits-2.0.4-LICENSE) |
| `isarray` | 1.0.0 | `MIT` | [isarray-1.0.0-README.md](LICENSES/node/isarray-1.0.0-README.md) |
| `jszip` | 3.10.1 | `(MIT OR GPL-3.0-or-later)` | [jszip-3.10.1-LICENSE.markdown](LICENSES/node/jszip-3.10.1-LICENSE.markdown) |
| `lie` | 3.3.0 | `MIT` | [lie-3.3.0-license.md](LICENSES/node/lie-3.3.0-license.md) |
| `pako` | 1.0.11 | `(MIT AND Zlib)` | [pako-1.0.11-LICENSE](LICENSES/node/pako-1.0.11-LICENSE)、[pako-1.0.11-Zlib-NOTICE.txt](LICENSES/node/pako-1.0.11-Zlib-NOTICE.txt) |
| `pptxgenjs` | 4.0.1 | `MIT` | [pptxgenjs-4.0.1-LICENSE](LICENSES/node/pptxgenjs-4.0.1-LICENSE) |
| `process-nextick-args` | 2.0.1 | `MIT` | [process-nextick-args-2.0.1-license.md](LICENSES/node/process-nextick-args-2.0.1-license.md) |
| `readable-stream` | 2.3.8 | `MIT` | [readable-stream-2.3.8-LICENSE](LICENSES/node/readable-stream-2.3.8-LICENSE) |
| `safe-buffer` | 5.1.2 | `MIT` | [safe-buffer-5.1.2-LICENSE](LICENSES/node/safe-buffer-5.1.2-LICENSE) |
| `setimmediate` | 1.0.5 | `MIT` | [setimmediate-1.0.5-LICENSE.txt](LICENSES/node/setimmediate-1.0.5-LICENSE.txt) |
| `string_decoder` | 1.1.1 | `MIT` | [string_decoder-1.1.1-LICENSE](LICENSES/node/string_decoder-1.1.1-LICENSE) |
| `undici-types` | 6.21.0 | `MIT` | [undici-types-6.21.0-LICENSE](LICENSES/node/undici-types-6.21.0-LICENSE) |
| `util-deprecate` | 1.0.2 | `MIT` | [util-deprecate-1.0.2-LICENSE](LICENSES/node/util-deprecate-1.0.2-LICENSE) |

## 特殊情况

- **isarray**：原包没有独立 LICENSE，完整 MIT 和既有版权行在 README；本包复制完整原 README。
- **jszip**：原声明为 MIT OR GPL-3.0-or-later，完整原文件保留两种文本，不把它改写成单一 MIT 声明。
- **pako**：原声明为 MIT AND Zlib。其独立 LICENSE 及 lib/zlib 原版权/许可头都保留；LICENSES 额外保存一份字节不改的 Zlib 头部。
- **https 1.0.0**：官方 npm tarball 只有一个 331 字节的 package/package.json，声明 ISC，没有许可全文、README 或实现文件。为保持内部运行文件不变，本次运行附件保留该原元数据文件；源码包不携带 node_modules。锁文件仍保留真实上游依赖关系。当前 PptxGenJS 的 CJS/ES 代码使用 `import('node:https')`，宿主 Node 内置模块提供该能力。此前本地 Node 22.21.0 已确认 `require.resolve('https') === 'https'`。该处置是发行范围及模块解析事实，不是对缺失原文的法律认证；没有凭作者字段补造版权行。

依赖许可原文此前已从 [官方 npm registry](https://registry.npmjs.org/) 获取 tarball 核对。精确版本链接、tarball SHA-256、registry 完整性及原文副本 SHA-256 见 [dependency/SBOM.json](dependency/SBOM.json) 与 [LICENSES/SOURCE_INDEX.json](LICENSES/SOURCE_INDEX.json)。原文与对应上游版本逐字节核对；未修改运行 node_modules 源码或许可文件。

## 宿主提供的依赖

Python、Node.js 可执行程序、Pillow、NumPy 和字体不随这两个发行包捆绑。`compat-runtime/requirements.txt` 声明 `Pillow>=10`、`numpy>=1.24`，实际环境适用实际安装版本及其依赖的许可。

- [Pillow 官方许可说明](https://pillow.readthedocs.io/en/stable/about.html#license)
- [NumPy 官方许可说明](https://numpy.org/doc/stable/license.html)
- [Node.js 原许可](https://github.com/nodejs/node/blob/main/LICENSE)

本清单核实随包声明、来源与字节，不替代权利确认，也不表示上游作者认可本项目或输出素材。用户输入、模型生成内容与外部素材不因源码采用 MIT 就自动获得相同授权。
