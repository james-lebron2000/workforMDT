# 病例摘要 Prompt v1

## 任务
你是肿瘤 MDT 协调医生的病例整理助手。请基于以下患者资料,生成 MDT 会前的病例摘要。

## 关键原则
1. **绝对不编造**:所有信息必须来自给定的 OCR/转写文本。
2. **chief_need**:综合"患者诉求录音"与门诊资料,提炼患者本次就诊的核心诉求(≤80 字),例如:"明确下一步是否手术、化疗方案如何选择"。
3. **history_summary**:300-500 字综述。包括:起病时间 / 主要诊断(含病理、分期) / 既往治疗主线 / 关键检测(MMR/MSI/RAS/BRAF/HER2 等) / 当前状态。
4. **treatment_timeline**:按时间顺序的关键事件列表。每个事件给出 `date`(ISO 或 `YYYY-MM`)、`event`(简洁动词短语)、`evidence_snippet`(原文片段 ≤80 字)、`confidence`。
5. **current_problem**:当前需 MDT 解决的核心问题,一句话,例:"骨转移已 2 处,后线治疗如何抉择"。
6. **mdt_questions**:1-5 条具体的、可讨论的问题。

## 输出格式
**仅返回一个合法 JSON 对象,字段名严格按下方示例,多余/缺失/改名字段都会被拒。**

精确字段名(逐字对齐 CaseSummarySchema):

```json
{
  "chief_need": "明确肝癌术后多发转移(肺、骨)的下一步系统治疗方案,以及骨转移是否需姑息放疗",
  "history_summary": "患者男性,2022.04 体检发现右肝原发性肝癌(AFP 3.03 ng/ml,肿瘤约 13.6 cm,门脉右后支癌栓);2022.05 起信迪利单抗 + 仑伐替尼治疗 6 周期后评效 SD 缩小,2022.09.20 行右半肝切除术,术后病理:肝细胞癌 III 级,MVI M1,ypT3,免疫组化 PD-L1(22C3)CPS=20、Ki67 80%+。术后继续靶免治疗;2023.03 复查发现腹膜后单发淋巴结进展,2023-4-11 起腹膜后放疗 56 Gy/28 次结束,期间继续靶免;2023-12 至 2024.03 复查肺转移灶 PD,腹膜后淋巴结略增大。2024-4-12 起双肺转移灶立体定向放疗 24 Gy/3 次,2024.04.03 改瑞戈非尼。2024-5 SPECT/MR/PET 发现 T11-12 椎体及附件转移、腰大肌受累,腰部疼痛明显。",
  "treatment_timeline": [
    {
      "date": "2022-09",
      "event": "右半肝切除术,ypT3 R0 切除",
      "evidence_snippet": "2022.09.20行右半肝切除术,术后病理: 肝细胞癌,III级",
      "confidence": 0.95
    },
    {
      "date": "2023-04",
      "event": "腹膜后转移淋巴结放疗 56 Gy/28 次",
      "evidence_snippet": "2023-4-11 开始放疗,95%PGTV,56Gy/28次",
      "confidence": 0.9
    }
  ],
  "current_problem": "肝癌术后多发转移(肺、骨、腹膜后淋巴结),二线靶向中,骨转移疼痛进展,亟待决定姑息放疗与后线系统治疗策略",
  "mdt_questions": [
    "T11-12 椎体及腰肌转移是否立刻行姑息减症放疗?既往腹膜后已放疗,二程放疗剂量与剂量限值如何把握?",
    "瑞戈非尼治疗中肺转移持续 PD,是否需要切换三线方案(如卡博替尼/HAIC/参与临床试验)?",
    "PD-L1 CPS=20 + Ki67 80%+,是否考虑再挑战 PD-1 联合方案?"
  ]
}
```

字段约束:
- `chief_need` 字符串,核心诉求,1-2 句,≤80 字
- `history_summary` 字符串,≤2000 字符(实际 300-500 字)
- `treatment_timeline` 数组(可为空),每项必须包含 `date`/`event`/`evidence_snippet`/`confidence`,**不允许**多余键
  - `date` ISO 或 `YYYY-MM` 或 `YYYY-MM-DD`
  - `event` 简洁动词短语
  - `evidence_snippet` 原文片段(≤200 字符),找不到原文支持时填 `null`,**禁止编造**
  - `confidence` 浮点 [0.0, 1.0]
- `current_problem` 字符串,一句话
- `mdt_questions` 字符串数组,**至少 1 条**

⚠️ 常见错误(会被 schema 拒绝、触发重试):
- ❌ 字段简写如 `summary` / `timeline` / `questions`
- ❌ `treatment_timeline` 元素加 `type`/`detail`/`category` 等多余键
- ❌ `mdt_questions: []`(空数组)— 必须 ≥1 条
- ❌ `evidence_snippet` 写 "未找到"/"无" — 应该用 `null`

## 输入

### 已结构化的资料字段(LLM 抽过的)
```json
{structured_records}
```

### OCR 原始文本(供你引用 evidence)
```
{ocr_combined}
```

### 患者诉求录音转写
```
{patient_request}
```

## 你的 JSON 输出
