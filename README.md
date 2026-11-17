# 知译 PDF（ZhiYi PDF）2.0

在 Zotero 中翻译论文 PDF：保留原版面，生成纯译文和双语对照，作为附件加入原文献条目。

2.0 是一次重写。1.x 在 PDF2zh / BabelDOC 之上打补丁，绕不开它们逐块翻译、逐块排版的结构；2.0 换成自研引擎，从三个老问题入手：

| 1.x 的问题 | 原因 | 2.0 的做法 |
| --- | --- | --- |
| 跨栏、跨页的段落翻得很差 | 段落在栏底被切成两块，两半各自翻译 | 按阅读顺序把被栏、页、行间公式切开的段落重新连成一段，**整段**交给翻译，再像连续的文本框一样把译文依次排回各个位置 |
| 排版错乱 | 版面识别框直接当作段落，框不准时段落被拆开或粘连 | 版面模型只用来**判断区域类型**，段落边界由行距、首行缩进、悬挂缩进、行尾留白等排版特征决定 |
| 字号忽大忽小 | 每个文本框各自缩小字号直到装下 | 字号和行距按**样式组**（全部正文、全部图注、每个表格…）统一求解；只有极少数实在放不下的段落才单独缩小，并在报告中列出 |

此外：

- **公式**：只有真正的数学符号（数学字体、上下标、无法用文字表示的字形）才作为公式原样保留；数字、百分号、引用编号留在句子里交给翻译，不再“乱飞”。行内公式以矢量形式从原页面复制到译文中。
- **术语**：使用大模型时，先通读全文抽取术语表，整篇论文统一译法；也可以填写自己的术语。
- **参考文献、页眉页脚、图片、行间公式**保持原样。
- 翻译结果有本地缓存，重新生成或换版式不必重复付费。

## 安装

需要 macOS（已在 Zotero 10 上开发；Linux 应可用但未验证，Windows 暂不支持自动启动服务）。

1. **安装引擎**：下载同版本源码包并解压，双击 **`安装 知译 PDF 引擎.command`**（或在终端运行 `bash scripts/install.sh`）。
   - 引擎装在 `~/.zhiyi`，有独立的 Python 环境，不会改动 PDF2zh、知译 1.x（`~/.pdf2rea`）或系统 Python。
   - 首次会下载版面模型和字体（约 300 MB）；用过 PDF2zh 的机器会直接复用它的缓存。
   - 有 [uv](https://docs.astral.sh/uv/) 时更快；没有则使用 Python 3.10+ 自带的 venv。
2. **安装插件**：在 [Releases](https://github.com/giaogiaogiaoluan/zhiyi-pdf/releases) 下载 `ZhiYi-PDF-2.0.0.xpi`，Zotero → 工具 → 插件 → 齿轮 → 从文件安装插件。从 1.x 直接覆盖安装即可，已保存的 API 设置会自动迁移。
3. **选择翻译服务**：Zotero 设置 → 知译 PDF → 翻译服务，点击“测试翻译服务”确认可用。

升级引擎时重新运行安装脚本即可。

## 使用

在文献库里选中文献条目或 PDF 附件，右键 **知译 PDF：翻译**。进度窗口会显示每篇论文的处理阶段；完成后译文作为附件加入条目，并给出摘要（段落数、合并了多少跨栏/跨页段落、有无单独缩小字号的段落）。

### 翻译服务

| 方式 | 说明 |
| --- | --- |
| 大模型 API（推荐） | 任何 OpenAI 兼容接口：DeepSeek、硅基流动、火山方舟、阿里云百炼、智谱、OpenAI、本机 Ollama 等。整段翻译、术语统一和格式标记都依赖它。费用由所选服务按用量计。 |
| 谷歌翻译 | 免密钥，适合试用。译文质量一般，可能限流，不支持自动术语表。 |

1.x 中的“免费翻译（SiliconFlow Free）”是 PDF2zh 官方流程专用的代理通道，2.0 无法使用。

### 输出

- **纯译文**：原版面，文字替换为译文。
- **双语对照**：左右并排（原文在左），或原文、译文逐页交替。

### 使用边界

- 扫描版 PDF 没有文字层，需要先做 OCR。
- 竖排、旋转的文字和图片中的文字保持原样。
- 复杂表格（合并单元格、极窄的列）中放不下的单元格会保留原文。
- 版面识别依赖模型，少见的版式仍可能出错；每次翻译都会在输出目录生成 `*.report.json`，列出合并、缩字、保留原文的位置，方便检查。

## 命令行

引擎也可以单独使用：

```sh
~/.zhiyi/venv/bin/python -m zhiyi paper.pdf -o out/ \
  --service openai --base-url https://api.deepseek.com/v1 --model deepseek-chat --api-key sk-…
~/.zhiyi/venv/bin/python -m zhiyi paper.pdf -o out/ --service google --pages 1-3
~/.zhiyi/venv/bin/python -m zhiyi service status
```

`--help` 列出全部选项（术语表、双语版式、是否翻译表格与参考文献等）。

## 工作原理

```
PDF ─► 字形抽取（PyMuPDF） ─► 版面区域（DocLayout-YOLO）
    ─► 行 ─► 段落片段（按行距、缩进、留白分段） ─► 阅读顺序（多栏）
    ─► 段落链接（跨栏 / 跨页 / 跨行间公式） ─► 行内公式识别 ─► 带占位符的标记文本
    ─► 批量翻译（术语表、上文语境、缓存、失败重试）
    ─► 按样式组统一字号与行距 ─► 中文排版（标点禁则、中西文间距、两端对齐）
    ─► 精确擦除原文 ─► 写入译文与公式 ─► 纯译文 / 双语对照
```

代码在 `engine/zhiyi/`，结构见 [开发说明](docs/development.md)。

## 开发

```sh
bash scripts/run_tests.sh      # 引擎 pytest（合成 PDF，不需联网和模型）+ 插件 node 测试
bash scripts/build_xpi.sh      # 测试通过后生成 dist/ZhiYi-PDF-<版本>.xpi
```

## 许可证与致谢

本项目按 **GNU AGPL-3.0** 分发，见 [LICENSE](LICENSE) 与 [第三方说明](THIRD_PARTY_NOTICES.md)。

感谢 [PyMuPDF](https://github.com/pymupdf/PyMuPDF)、[DocLayout-YOLO](https://github.com/opendatalab/DocLayout-YOLO)，以及 1.x 所基于的 [zotero-pdf2zh](https://github.com/guaguastandup/zotero-pdf2zh)、[PDFMathTranslate-next](https://github.com/PDFMathTranslate-next/PDFMathTranslate-next)、[BabelDOC](https://github.com/funstory-ai/BabelDOC) 的作者和贡献者。
