# 开发说明

知译 PDF 2 由两部分组成：

- **引擎**（`engine/zhiyi/`，Python）：分析 PDF、翻译、排版、写出结果，并提供本机 HTTP 服务；
- **插件**（`plugin/`，Zotero 7/10 bootstrapped 插件）：菜单、设置页、提交任务、把结果加入文献库。

## 引擎模块

按处理顺序：

| 模块 | 作用 |
| --- | --- |
| `extract.py` | 用 PyMuPDF `rawdict` 取得每个字形的位置、字号、字体，把 MuPDF 拆碎的片段拼回视觉行；标记上下标 |
| `regions.py` | DocLayout-YOLO（ONNX）检测区域：正文、标题、图、表、行间公式、图表标题、页眉页脚 |
| `layout.py` | 给行贴上区域标签；把同栏上下相邻的行堆叠起来，再按行距、首行缩进、悬挂缩进和行尾留白切成段落片段（`Block`）；判断标题、脚注、列表等角色 |
| `order.py` | 多栏阅读顺序（找栏间空白，跨栏元素作为分隔带）；计算每个片段可以占用的宽度和向下延伸的界限 |
| `chain.py` | 把被栏、页、行间公式切断的片段链接成一个逻辑段落（`Unit`） |
| `formula.py` | 行内公式识别：数学字体、上下标、无法用文字表示的字形才算公式；数字、运算符按上下文并入 |
| `markup.py` | 生成发给翻译的标记文本（`{v1}` 行内公式、`{eq1}` 行间公式位置、`<b> <i> <sup> <sub> <a>` 样式）；校验并修复译文中的标记 |
| `analyze.py` | 串起以上步骤，识别参考文献（默认不翻译） |
| `translate/` | 后端（OpenAI 兼容、谷歌、测试用的 pseudo 和 file）；批量提示词、自动术语表、语境、SQLite 缓存、漏译检测与重试 |
| `typeset.py` | 分词成不可断开的片段（拉丁词、数字、标点禁则），贪心断行，两端对齐；按样式组统一求解字号和行距，个别段落按“收紧行距 → 用下方空白 → 缩字”的顺序兜底 |
| `render.py` | 用细条擦除区精确删除原文字形（避开需要保留的字形）；写入译文；从“只剩行内公式”的副本中以矢量方式剪贴公式；生成双语对照 |
| `pipeline.py` | 单个 PDF 的完整流程和 `*.report.json` |
| `server.py` / `service.py` / `cli.py` | 本机 HTTP 服务、后台启停、命令行 |
| `assets.py` / `fonts.py` | 模型与字体的查找和下载（优先复用 `~/.cache/babeldoc`）；拉丁/中文字体按字符选择 |

## 设计约定

1. **一致优先**：字号、行距按样式组求解，不按段落求解。新增兜底逻辑时，不要让单个段落悄悄改变字号；必须改变时写入 `unit.notes` 和报告。
2. **模型只定类型，排版特征定边界**：不要用检测框直接当作段落。
3. **宁可保留原文，不要排错**：识别不确定的文字（非正文的短行、旋转文字、放不下的单元格）保持原样。
4. **标记必须可验证**：译文中占位符丢失、重复或标签不配对时，`markup.repair` 修复或降级，并记录原因。
5. 服务只监听回环地址，只接受 JSON 请求，并校验 `Host` 头。

## 本机服务协议

| 请求 | 说明 |
| --- | --- |
| `GET /health` | `{"status": "ok", "name": "zhiyi", "version": …}` |
| `POST /translate` | `{"fileName", "fileContent"(base64), "config": {…Config 字段…}}` → `{"status": "accepted", "taskId"}`；也兼容 1.x 插件的字段名 |
| `GET /api/tasks` | 排队和进行中的任务（`percent`、`message`） |
| `GET /api/history` | 已结束的任务（`fileList`、`filePaths`、`summary` 或 `error`） |
| `GET /translatedFile/<文件名>` | 下载结果 |
| `POST /api/test` | 用当前配置翻译一句话 |

`Config` 字段见 `engine/zhiyi/config.py`。

## 开发环境与测试

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e "engine[dev]"
bash scripts/run_tests.sh
```

引擎测试使用合成的双栏 PDF（`engine/tests/conftest.py`），并替换掉版面模型，不需要联网或下载模型。插件测试在 Node 中用模拟的 Zotero 对象运行 `pdf2rea.js`。

真实论文的检查：

```sh
PYTHONPATH=engine .venv/bin/python -m zhiyi paper.pdf -o out/ --service google
```

然后查看 `out/*.report.json`：`layout.groups` 是各样式组的字号和行距，`notes` 列出有问题的段落，`layout.stuck_glyphs` 是未能精确擦除的原文字形数。

## 发布

1. 同步三处版本号：`plugin/manifest.json`、`plugin/content/scripts/pdf2rea.js` 的 `PLUGIN_VERSION`、`engine/zhiyi/__init__.py`（构建脚本会检查）。插件只会自动重启与自己版本一致的引擎。
2. 更新 `CHANGELOG.md`。
3. `bash scripts/build_xpi.sh`，在 Zotero 中安装 `dist/` 下的 XPI，用真实条目翻译一篇论文。
4. 发布 XPI 与同版本源码包，更新 `updates.json` 中的版本、下载地址和 `sha256`。
