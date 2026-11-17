# 第三方项目与来源说明

知译 PDF 以 **GNU Affero General Public License v3.0（AGPL-3.0）** 分发，许可全文见 [LICENSE](LICENSE)。本软件不提供许可证第 15、16 条所述保证。再分发修改版本时，请保留来源、版权、许可与修改说明，并提供对应源码。

## 运行时依赖（由安装脚本从 PyPI 安装，不随 XPI 分发）

| 项目 | 用途 | 许可 |
| --- | --- | --- |
| [PyMuPDF](https://github.com/pymupdf/PyMuPDF) | PDF 读取、渲染、文字擦除与写入 | AGPL-3.0（或 Artifex 商业许可） |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime) | 运行版面模型 | MIT |
| [NumPy](https://numpy.org) | 图像张量 | BSD-3-Clause |
| [HTTPX](https://www.python-httpx.org) | 翻译服务与资源下载 | BSD-3-Clause |
| [fontTools](https://github.com/fonttools/fonttools) | 字体处理 | MIT |

## 模型与字体（首次运行时下载，不随本仓库或 XPI 分发）

| 资源 | 来源 | 许可 |
| --- | --- | --- |
| DocLayout-YOLO（DocStructBench）ONNX 版面模型 | [opendatalab/DocLayout-YOLO](https://github.com/opendatalab/DocLayout-YOLO)；ONNX 导出 [wybxc/DocLayout-YOLO-DocStructBench-onnx](https://huggingface.co/wybxc/DocLayout-YOLO-DocStructBench-onnx) | AGPL-3.0 |
| 思源宋体 / 思源黑体（Source Han Serif / Sans CN） | Adobe | SIL Open Font License 1.1 |
| Noto Serif / Noto Sans | Google | SIL Open Font License 1.1 |

模型与字体从 ModelScope、HF-Mirror、Hugging Face 或 [BabelDOC-Assets](https://github.com/funstory-ai/BabelDOC-Assets) 镜像下载；本机已有 BabelDOC 缓存（`~/.cache/babeldoc`）时直接复用。

## 1.x 的上游项目

知译 PDF 1.x 基于 [zotero-pdf2zh](https://github.com/guaguastandup/zotero-pdf2zh)（v4.1.7）、[PDFMathTranslate-next](https://github.com/PDFMathTranslate-next/PDFMathTranslate-next)（2.9.0）与 [BabelDOC](https://github.com/funstory-ai/BabelDOC)（0.6.2）开发，三者均为 AGPL-3.0。2.0 的引擎和插件为重新编写的代码，不再包含或调用上述项目；插件内部标识、设置前缀沿用 1.x，以便原地升级。感谢上游作者与贡献者在 1.x 阶段提供的基础工作。

## 发布边界

- 本仓库公开插件、引擎源代码、安装与构建脚本和文档。
- Zotero、Python 环境、第三方依赖、模型、字体、用户配置、API 密钥、文献库、PDF、译文和缓存均不随本项目发布。
- 第三方翻译服务的可用性、计费与使用规则由服务提供方决定。

源代码与版本发布：<https://github.com/giaogiaogiaoluan/zhiyi-pdf>。
