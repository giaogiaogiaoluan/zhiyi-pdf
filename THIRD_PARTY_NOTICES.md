# 第三方项目与来源说明

**知译 PDF（ZhiYi PDF）基于 PDF2zh 生态开发，参考并兼容 [guaguastandup/zotero-pdf2zh](https://github.com/guaguastandup/zotero-pdf2zh) v4.1.7 的服务协议与工作流程。** 感谢上游作者与贡献者提供的基础工作。

知译 PDF 是独立维护的兼容实现，不是上游官方版本，也不声称完整复刻或完整 fork。公开版本：**1.1.1**；本次整理与增强修改日期：**2026-10-22**。维护账号：`giaogiaogiaoluan`。

本仓库及其自有增强代码以 **GNU Affero General Public License v3.0（AGPL-3.0）** 分发，许可全文见 [LICENSE](LICENSE)。你可以按该许可证使用、修改和再分发；本软件不提供许可证第 15、16 条所述保证。再分发修改版本时，请保留适用的来源、版权、许可与修改说明，并提供对应源码。

## zotero-pdf2zh

| 项目 | 说明 |
| --- | --- |
| 作者 | guaguastandup 及贡献者 |
| 官方项目 | <https://github.com/guaguastandup/zotero-pdf2zh> |
| 兼容基线 | v4.1.7 |
| 上游许可 | [v4.1.7/LICENSE](https://github.com/guaguastandup/zotero-pdf2zh/blob/v4.1.7/LICENSE)，AGPL-3.0 |

知译 PDF 参考其本地服务请求、异步任务轮询、产物命名及附件工作流，重写 Zotero 客户端，提供翻译、裁剪、双语对照与裁剪并对照四种入口，并加入独立配置、排版增强和诊断。它不覆盖上游的全部功能。

上游服务程序、原插件安装包及虚拟环境不在本仓库或 XPI 内。运行环境准备脚本从用户已安装的上游服务只读复制一份独立环境，并仅调整该副本的配置与缓存路径。复制和使用的上游文件继续遵守其原许可证，原版权归上游作者及贡献者所有。

## BabelDOC

| 项目 | 说明 |
| --- | --- |
| 作者 | funstory-ai / awwaawwa 及贡献者 |
| 官方项目 | <https://github.com/funstory-ai/BabelDOC> |
| 已验证版本 | 0.6.2 |
| 上游许可 | [v0.6.2/LICENSE](https://github.com/funstory-ai/BabelDOC/blob/v0.6.2/LICENSE)，AGPL-3.0 |

知译 PDF 的 Python 增强组件配合其文档结构、翻译与排版接口运行。自有增强包括跨页段落合译、结构化文本与表格处理、术语和数字一致性、引用与符号处理，以及运行诊断；修改和兼容调整记录见 [CHANGELOG.md](CHANGELOG.md)。本仓库和 XPI 不内置 BabelDOC 完整安装环境、模型权重、字体或翻译缓存。

## PDFMathTranslate-next / pdf2zh_next

| 项目 | 说明 |
| --- | --- |
| 作者 | PDFMathTranslate-next / awwaawwa 及贡献者 |
| 官方项目 | <https://github.com/PDFMathTranslate-next/PDFMathTranslate-next> |
| 已验证版本 | 2.9.0 |
| 上游许可 | [LICENSE](https://github.com/PDFMathTranslate-next/PDFMathTranslate-next/blob/main/LICENSE)，AGPL-3.0；2.9.0 安装包元数据同样声明 AGPL-3.0 |

本项目通过本地翻译服务使用该引擎，其完整环境不随 XPI 分发。独立环境准备脚本中的缓存和配置路径适配仅作用于用户创建的副本；这些适配的源代码随脚本公开。

## 发布内容与依赖边界

- 本仓库公开插件源代码、自有 Python 增强组件、术语表、构建和安装脚本及必要文档；对应版本源码与安装包一同提供。
- Zotero 应用、原 PDF2zh 插件安装包、上游服务运行环境、Python 虚拟环境、第三方依赖、模型、字体、用户配置、API 密钥、文献库、PDF、译文和缓存均不随本项目发布。
- 用户另行安装的组件遵守各自许可证。免费接口或第三方模型服务的可用性及使用规则由服务提供方决定；开源本插件不授予第三方服务的使用权。
- 客户端使用自绘 SVG 图标，没有随包分发上游 PDF2zh 图标、`zotero-plugin-toolkit` 或 `axios`。

源代码与版本发布：<https://github.com/giaogiaogiaoluan/zhiyi-pdf>。
