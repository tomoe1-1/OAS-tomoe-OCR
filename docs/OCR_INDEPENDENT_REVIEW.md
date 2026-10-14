# OCR 优化独立只读审查

审查对象为 candidate 中四个 Python；未改生产安装或候选，由主代理修复发现。真实依赖读取包括 RuleOcr 多继承、BaseTask.ocr_appear、Full/Single、merge_area、Paddle TextSystem、RPC 竖排识别及 OCR 日志。检查调用方包括 Delegation、SwitchSoul、Moonlight、MartialArts、活动次数与 Dokan。

发现与处理：

1. 初版低分验证使用 NFKC 而实际后处理未规范化，会救回 `①`、`²` 并在 int 转换时报错。独立负例复现，最终 validator 仅验证原始 ASCII 表达式，已修复。
2. 新完整分块匹配暴露 Full 原有面积合并错误：将 width/height 传入需要 endpoints 的 merge_area，`探`、`索` 双框错误返回第一框范围。通过真实 RuleOcr + Full 测试复现；最终 sub_ocr 使用全部匹配 polygon 的 min/max 求 union，已修复。
3. 空白/恒色图会被模型猜字；ppocr 只拒绝完全均匀/零面积输入，保留有任意变化图进入原模型路径；custom 竖排检测同样受保护，返回类型不变。

最终验证：

- `review_checks.py`：27 项通过，完整/部分关键词、同字重复、逆序输入横竖分块、错序、行列分离、远距离、近邻歧义、重复完整目标仅首个、零框、非有限框、超过 128 框、数字/计数/数量格式与 Unicode 圈号负例。
- `review_real.py`：使用独立副本 toolkit，实际 RuleOcr/Full/Single/后处理 MRO 及候选方法共 22 项通过。仅 OCR 推理边界被替代，没有 Device、输入或服务启动。覆盖数字低分接收/拒绝、原高分行为保留、Full 面积/重复目标、四种空或恒色输入不调用模型、非均匀图仍调用原模型。
- 实际导入证明复制运行库接口兼容；原安装目录导入因 logger chdir 的 WinError 5 失败，不将该次失败算通过。独立运行库导入产生独立目录日志，不修改原安装。

最终候选 SHA256：

| 文件 | SHA256 |
|---|---|
| base_ocr.py | 3fd6e998676ba60ffcba6692a2a0bcfa9ac2e5e0d80ffabdd664cbd8deafa9a5 |
| ppocr.py | e3e0f77c8c6909ee61adfda3a9fa7091397f63754da8743dc570be95bf69aba4 |
| sub_ocr.py | 6a7d8472d08bd495da62ab0f68fc8daca2ceae4f6476e0d494fc9bc893eb05b1 |
| text_validation.py | 2b5088ba79087b377a33537a97e8dcc9e732ff8438878321152ae9c628872453 |

封装脚本审查：验证便携母包 SHA、拒绝覆盖现存目录、路径穿越检查、原始 OCR 基线比较、tasks overlays 源字节二次比较、源码 UTF-8/BOM/compile、最终完整 manifest。当前脚本初次只复制两个候选文件，主代理明确后续同步四文件并重建完整 manifest，必须最终逐项核对。已读取新 launcher 相对 `%~dp0` 启动且 web22388；deploy/template 与 config/deploy.yaml 的 OCR client/server 同为22368、image client/server 同为22369，AutoConnect/ReplaceAdb false，Run null。未核验未完成的最终整包 manifest 或进程实际监听。

结论：上述四候选文件未发现确定性阻塞。空值仍保留 int=0、counter=(0,0,0)，Dokan unknown=-1 的图片判定未改，避免破坏任务调用方。高分问号/箭头猜数字、空值与真实 0 不可区分仍为旧风险；本次不称已经解决全部 OCR 错字。分块匹配使用有限几何启发，复杂布局/艺术字体必须自然游戏验证。未跑全仓测试、任务实机或最终服务启动；模型精度提升须以真实标注样本推理报告为依据，不能由这些接口测试证明。

## 最终封装补充审查

已复读实际 finalize.py、独立版 verify_deployment.py 和 final-bytes.json。finalize 仅写新 ROOT，四候选逐项 UTF-8/BOM/compile 后复制并回读核对，更新四源码、OCR_OPTIMIZATION 与 verifier 的 SHA；log/data/configs 为可变运行数据从 manifest 排除。未调用 Git更新、旧安装、Device 或服务。Windows verifier 保留 resolve 后 ROOT 归属检查和绝对路径拒绝，之后仅在文件读取时加 `\\?\` 前缀，未放宽路径检查；长路径支持不会改变哈希检查语义。

final-bytes.json 四 SHA 与本报告一致。实际 integrity-final.txt 已报告 23095 文件校验通过。主代理实际 verify-final.txt 报告 27 检查、54 正例全正确、64 负例仍有 2 个误文字；13 均匀图负例误文字为 0，不得把该结果宣传成零误识别。

最终推理日志模型路径全部是新 ROOT 的 toolkit/.paddlex/official_models，不依赖原安装模型目录。config 下只有 deploy.yaml/template.json 无账号配置。显式新 deploy 与 template 的 client/server 配对一致，launcher 仅新 web22388，Run null，不自动启动账号。代码默认地址仍是旧兼容 22268，在本包显式配置下不会触发；用户将 OcrClientAddress 手工清空等配置污染不属于本次验证覆盖，不能宣称任意改配置均隔离。未做实际监听/服务连接或游戏验收。最终封装两处未发现确定性阻塞。

最终字节复核：主代理统一 LF 并移除 sub_ocr 的未用 merge_area 导入后，已重读 final-bytes.json 与候选，四 candidate/ROOT SHA 逐项全部相同（表中为此次最终值）。27 项独立函数验证与 22 项实际接口验证再次执行全部通过；此变化未改变业务语义。前述整包日志属于此前校验，主代理正重新执行整包与新增消费者检查，本审查不将尚未读取的结果算通过。
