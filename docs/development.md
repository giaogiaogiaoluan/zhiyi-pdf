# 开发说明

知译 PDF 由 Zotero 客户端与 Python 翻译增强组件组成。客户端兼容 zotero-pdf2zh Server 4.1.7 的主要任务协议，增强组件在独立翻译进程中加载。上游服务、引擎依赖及模型不随仓库分发，来源和许可证见 [第三方说明](../THIRD_PARTY_NOTICES.md)。

## 源码结构

| 路径 | 内容 |
| --- | --- |
| `plugin/manifest.json` | 插件名称、版本和兼容范围 |
| `plugin/bootstrap.js` | Zotero 生命周期与资源注册 |
| `plugin/prefs.js` | 默认偏好设置 |
| `plugin/content/preferences.xhtml` | 原生设置页 |
| `plugin/content/scripts/pdf2rea.js` | 菜单、偏好、任务及产物管理 |
| `plugin/content/runtime/service_control.py` | 独立本机服务控制 |
| `engine/pdf2rea_engine/` | Python 翻译增强组件及术语 CSV |
| `scripts/` | 环境准备、测试、校验与打包 |
| `tests/` | 合成文本、合成 PDF 和协议契约测试 |

构建脚本将 `engine/pdf2rea_engine/` 复制到 `plugin/content/engine/`，生成文件清单后打入 XPI。请编辑 `engine/` 中的源文件，避免仅修改构建生成的副本。

## 开发环境

源码构建需要 Python 3、Node.js、Bash 和 rsync。真实引擎检查另外需要独立的 BabelDOC 0.6.2 / pdf2zh_next 2.9.0 环境。当前仅在 macOS + Zotero 10.0.6 完成实测，不能仅凭清单兼容范围推断其他平台已经验证。

首次准备独立环境：

```sh
python3 scripts/setup_runtime.py --source /path/to/zotero-pdf2zh/server
```

准备脚本只读使用源服务，目标默认为 `~/.pdf2rea/server`，拒绝覆盖已有目标目录。测试插件时，请使用独立 Zotero 配置和合成条目，不要把日常文献库作为测试夹具。

## 测试与构建

```sh
# 无真实引擎：纯逻辑、配置契约、JavaScript 与包结构检查
bash scripts/run_tests.sh --skip-wiring
bash scripts/build_xpi.sh --skip-wiring

# 有真实引擎：包含 BabelDOC 挂接和子进程检查
VENV_PY=/path/to/independent-venv/bin/python bash scripts/run_tests.sh
VENV_PY=/path/to/independent-venv/bin/python bash scripts/build_xpi.sh
```

`VENV_PY` 可指定解释器；`VENV` 兼容指定虚拟环境目录或解释器。测试状态默认写入临时目录，与日常服务配置、翻译缓存和术语记忆库隔离。运行合成 PDF 的完整流程比较可使用：

```sh
VENV_PY=/path/to/independent-venv/bin/python bash scripts/run_tests.sh --e2e
```

源码检查、真实引擎挂接、合成 PDF 流程和 Zotero 原生界面验证覆盖不同问题。报告结果时应明确实际完成的检查；`--skip-wiring` 不等于完整集成验证，也不能证明翻译模型质量。

## 兼容约定

1. 用户可见名称为“知译 PDF”，插件版本为 1.1.1；增强组件仍为 1.0.5。此版本不改变翻译算法。
2. 保留插件 ID `pdf2rea@qoder.dev`、偏好前缀 `extensions.zotero.pdf2rea.`、资源命名空间、Python 包 `pdf2rea_engine` 和独立运行路径，确保早期版本保留设置并直接升级。
3. 保留原有菜单任务、请求字段、异步轮询、产物命名和附件归属行为；显示文案与内部协议字段分开调整。
4. 免费服务与自有 API 的选择保持明确；选择免费服务时不附带其他提供方保存的密钥。
5. 原版 PDF2zh 的插件、服务与文献库保持独立。修改和测试应限制在本项目及独立环境中。

跨页、表格、引用符号等能力依赖引擎内部结构。升级 BabelDOC 或 pdf2zh_next 时，应先检查挂接位置与回退行为，再运行集成测试和渲染检查。

## 发布检查

- 更新插件清单、界面版本与中文更新记录，核对增强组件是否实际变化。
- 运行适用测试，检查包内文件清单及 XPI 安装、正常重启和设置页。
- 以合成条目验证菜单和附件工作流，确认没有写入原版运行环境。
- 只提交源码与通用文档；排除运行环境、密钥、文献库、PDF、译文、缓存、日志、备份和本机诊断记录。
- 发布 `dist/ZhiYi-PDF-<版本>.xpi` 及对应版本源码 ZIP，保留 LICENSE 和第三方说明。`dist/` 为本地构建目录，不纳入源码版本管理。
- 免费接口及第三方模型服务由提供方维护；发布本插件不意味着这些服务始终可用。
