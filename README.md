# OAS-tomoe-OCR

**OnmyojiAutoScript OCR 优化版** — 基于 [runhey/OnmyojiAutoScript](https://github.com/runhey/OnmyojiAutoScript) 制作。

版本 `2026.10.14-ocr.1`。父项目版本为 `runhey/OnmyojiAutoScript` 分支 `self`，源码继承提交 `8b488d6824b705065e8be79b37bdac0fc1f7d239`。

本仓库只做一件事：在不改动上游逻辑的前提下，把 OCR 识别结果的**校验与防误命中**做严，避免「文字识别到了但点错地方 / 点错范围」这类事故。

---

## 配套桌面控制端 OASX

本项目的图形化控制端由 **[tomoe1-1/OASX](https://github.com/tomoe1-1/OASX)** 提供，两者配套使用。

![OASX 桌面控制端](docs/images/oasx-desktop.jpg)

OASX 负责多账号配置、任务编排、日志统计与实时告警；OAS（本仓库）负责实际的游戏识别与点击执行。

| 组件 | 仓库 | 说明 |
|---|---|---|
| OAS（本项目） | [tomoe1-1/OAS-tomoe-OCR](https://github.com/tomoe1-1/OAS-tomoe-OCR) | Python 自动化主体，OCR 优化版 |
| OASX（控制端） | [tomoe1-1/OASX](https://github.com/tomoe1-1/OASX) | Flutter Windows 桌面控制端 |
| 上游父项目 | [runhey/OnmyojiAutoScript](https://github.com/runhey/OnmyojiAutoScript) | 本项目基于其制作 |

OASX 通过 HTTP 协议与本项目通信。本版端口：

| 服务 | 地址 |
|---|---|
| Web（OASX 连接入口） | `http://127.0.0.1:22388` |
| OCR 服务 | `127.0.0.1:22368` |
| 图片服务 | `127.0.0.1:22369` |

在 OASX 中把服务地址指向 `http://127.0.0.1:22388` 即可接管本项目。

---

## 本次优化了什么

只改 4 个文件，其余代码与上游保持一致。

| 文件 | 改动 |
|---|---|
| `module/ocr/base_ocr.py` | 低置信度数字**仅在整个结果符合数字 / 比例 / 数量语法时**才救回，拒绝「只要含一个数字就接受」的混合文本；低分比例要求 `current <= total` |
| `module/ocr/text_validation.py` | 完整关键词才匹配；横排与竖排文字框必须**相邻且对齐**才算命中（上限 128 框、有限链长）。不完整词、错序、重复单字、跨不相关行／列**都不能**拼成点击目标；多处完整目标只选一个，避免合并成大点击区域 |
| `module/ocr/sub_ocr.py` | Full 匹配框改用实际 polygon 极值合并，修复原代码把 `width`/`height` 误传给 endpoint 合并函数的错误 |
| `module/ocr/ppocr.py` | 完全恒色 / 零面积输入**直接返回无文本**，避免让无文字图进入模型猜字；对一般低对比、有变化图**不做**全局过滤 |

**保留不变**：现有 PP-OCRv6 medium ONNX 引擎与真实接口、竖排支持、既有游戏点击 / 恢复 / 新帧保护、Dokan 的图片判断与 `<=0` 退出逻辑。没有新增点击重试，没有新增自动任务，没有新增数字分类模型。

每个文件的改动前后 SHA256 记录在 [`OCR_OPTIMIZATION.json`](OCR_OPTIMIZATION.json)。

---

## 效果与限制

**已验证**：

- 123 张实机日志截图 + 人工标注存档于样本集；有效 **54 张清晰文字全部正确**，13 张恒色负例 **0 误文字**
- 64 张背景负例中仍有 **2 张产生误文字**
- 全目录 23095 个不可变文件校验通过；最终启动器 `server.py --help` 通过
- 4 个改动的 Python 文件拒绝 UTF-8 BOM，并以 `compile()` 验证通过

**未完成 / 限制**：

- ❌ **没有实机任务验收**，也没有全仓库全量测试
- 艺术字体、动画、裁剪不全、非均匀背景仍可能误识别
- 直接单行 recognizer 实测没有恢复 61 张原空结果中的高分正例，竖排还有退步，因此**未**默认切换
- 高置信数字的旧字符替换、空 OCR 与真实 `0` 共用哨兵，仍是兼容风险，本次没有全局更改这些调用协议
- 更严格的匹配可能拒绝较复杂的分块布局，需要自然运行截图反馈

样本不能证明全局 OCR 准确率提高。当前确认的是：减少部分词误命中、错误点击范围、不合法低分结果的接收，且现有清晰样本不退步。

---

## 快速开始

1. 从完整部署包解压到**新的短路径**（例如 `C:\yys\OAS-OCR`），不要覆盖已有安装
2. 进入解压后的 `OAS-OCR-2026.10.14` 目录，双击 `start-oas.bat`
3. 在 OASX 中连接 `http://127.0.0.1:22388`，配置或迁移自己的任务账号
4. **不要用旧的 `deploy.yaml` 覆盖本包配置** —— 本版 OCR 与图片服务使用新端口

本仓库只含**源码**。完整运行需要：

- 本仓库的业务源码与资源
- 内置 Python 3.10 运行库及全部依赖
- PP-OCRv6 medium det / rec ONNX 模型

三者齐全的完整部署包另行提供（`OAS-OCR-2026.10.14-ocr.1-full.zip`，约 500 MB，含运行库与模型）。

**同一模拟器上不要同时运行新旧两套自动化**，迁移前先停掉旧任务。

---

## 目录结构

```
module/ocr/          OCR 引擎与校验（本次主要改动位置）
tasks/               各功能任务（探索、悬赏、秘闻、狩猎、协战等）
assets/              图片识别资产
tests/               守卫测试
deploy/              部署与进程管理
config/              配置模板（不含账号信息）
docs/                文档与截图
    OCR_REVIEW.md            优化与验证报告
    OCR_INDEPENDENT_REVIEW.md 独立审查报告
    images/                  文档配图
OCR_OPTIMIZATION.json  4 个改动文件的前后 SHA256
RELEASE_VERSION.json   版本、端口与来源信息
```

`toolkit/`（Python 运行库与 ONNX 模型，约 918 MB）**不入库**，需随部署包提供。

---

## 许可

沿用上游项目的许可，见 [`LICENSE`](LICENSE)。