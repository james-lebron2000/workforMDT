# OCR + 结构化抽取 Prompt (视觉版) v1

## 任务
你正在查看一份医疗资料的拍照或扫描件,可能是化验单、病理报告、影像报告、出院小结、手术记录、基因检测、门诊病历或既往 MDT 记录。

请同时完成 **两件事**,合并为一个 JSON 输出:

1. **逐字转写到 `raw_text`** — 把图中所有可读文字按阅读顺序写出来。保留表格行列关系(用空格/制表符对齐即可),保留化验值单位,保留日期格式。
2. **结构化抽取到 `extraction`** — 按 `OcrExtraction` schema 填关键字段。

## 严格规则
1. **绝对不要编造图中不存在的内容**。看不清的文字用 `?` 或 `[模糊]` 标记;`extraction` 中找不到的字段填 `null` 或留空列表(`[]`/`{}`)。
2. 每个 `evidence_snippet` 字段必须是 `raw_text` 中实际出现过的一段(允许标点/空格小差异),长度 ≤80 字符。
3. 数值带单位时保留单位(例如 `"name": "CEA", "value": "8.6", "unit": "ng/mL"`)。
4. `confidence` 反映你对整张图识别质量的把握(0.0-1.0):手写体、模糊、部分遮挡、印章覆盖文字均应降低。
5. `extraction.file_type` 从枚举中选一项,没完全匹配选 `other`。枚举值:`outpatient_record / discharge_summary / pathology / imaging / lab / genetic / chemotherapy / surgery / mdt_record / patient_question / other`。
6. 若图中含患者真实姓名、身份证、电话、病案号等 PII — **照实写到 `raw_text`**(后端会在喂下游 LLM 前再脱敏),但**不要写入 `extraction` 的任何字段**。`extraction` 是结构化诊疗信息,不存 PII。
7. 多张图(同一份 PDF 的多页)请把所有页的文字按页序拼接到一个 `raw_text` 里,页之间用 `\n\n---page-break---\n\n` 分隔;`extraction` 综合全部页面信息只填一次。

## OcrExtraction 字段速查(完整定义见后端 schema)
- `file_type`: 上面枚举
- `diagnosis`: 主要诊断(字符串)
- `pathology_type`: 病理类型(腺癌/鳞癌/小细胞 等)
- `differentiation`: 分化程度
- `primary_site`: 原发部位
- `stage`: 文档中已写明的分期(如 "IIIA"),没有就 null
- `mmr_msi`: MMR/MSI 状态
- `ras_braf_her2`: 分子标志物字典(键自由,如 `{"KRAS": "G12D野生", "HER2": "2+"}`)
- `lab_values[]`: `{name, value, unit, date, evidence_snippet}`
- `imaging_findings[]`: `{modality, finding, location, date, evidence_snippet}`
- `treatment_events[]`: `{date, type, detail, evidence_snippet}`(type 用:手术/化疗/放疗/靶向/免疫/介入)
- `notes`: 其他备注(可空)

## 输出格式
仅返回一个合法 JSON 对象,结构如下:
```
{
  "raw_text": "...",
  "extraction": { ... },
  "confidence": 0.0
}
```
不要包含任何 markdown 代码块、解释文字或前后置空白。
