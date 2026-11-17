# PPT Rebuilder King源码说明

当前版本为2026.11.17-r28，运行身份为 `PPT Rebuilder King-20261117-controller-r28`。普通使用步骤见[首页](README.md)和[使用教程](docs/USAGE.zh-CN.md)。

## 文件组织

`compat-runtime`保存运行源码，`engineering`保存工程工具与测试，`tools`保存发行构建和校验工具。第三方许可位于`LICENSES`，依赖清单位于`dependency`。

ChatGPT运行ZIP携带运行程序与锁定的JavaScript依赖。源码ZIP提供开发文件，JavaScript依赖按`compat-runtime/package-lock.json`准备。Python、Pillow、NumPy、Node及字体由实际环境提供。

## 构建与校验

下载当前[运行ZIP](https://github.com/frederick-kk/ppt-rebuilder-king/releases/download/v2026.11.17-r28/PPT-Rebuilder-King-ChatGPT-20261117-controller-r28.zip)，放到源码目录外。构建工具读取该归档，核对文件清单与源码，然后复制运行ZIP的原始字节并独立构建源码ZIP。

```text
python tools/repack_runtime.py build --baseline ../downloads/PPT-Rebuilder-King-ChatGPT-20261117-controller-r28.zip --source . --output ../package-dist
python tools/repack_runtime.py verify --baseline ../downloads/PPT-Rebuilder-King-ChatGPT-20261117-controller-r28.zip --runtime ../package-dist/PPT-Rebuilder-King-ChatGPT-20261117-controller-r28.zip --source-archive ../package-dist/PPT-Rebuilder-King-20261117-r28-source.zip
```

运行归档共455项，包含425个运行负载文件和一份运行MANIFEST。构建输出使用程序目录外的新目录，运行ZIP与源码ZIP分别核对各自清单。

## 制作与测试

默认工作流依次完成背景、整页前景参考、一次页面描述、透明组件板和PPTX组装。主标题与其他艺术字由前景裁片组件板制作。整页前景由安全纯色去底，最终组件板保留原生RGBA。

```text
python -B -m unittest discover -s engineering/tests -v
python -B -m unittest discover -s tools -p test_repack_runtime.py -v
```

可选私有材料由测试环境变量传入，缺少材料时明确跳过。测试用例由开发者显式运行。当前实际验证范围见[验收说明](https://github.com/frederick-kk/ppt-rebuilder-king/blob/main/docs/RELEASE_VALIDATION.zh-CN.md)。

## 许可

第一方源码和文档采用[MIT](LICENSE)。第三方依赖及素材按各自许可使用，详见[NOTICE](NOTICE.md)。
