# 各科室意见提炼 Prompt v1

## 任务
基于一段已经按科室归类好的 MDT 会议转写,提炼每个科室的**核心意见**(opinion)、**理由**(rationale)、**具体建议**(recommendation)。

## 严格规则(不可妥协)
1. **绝不伪造**:如果某个科室在录音中没有发言或发言不涉及医学判断 → `is_missing=true`,其余字段填 null。
2. **必须覆盖**核心 6 科室:`外科` / `肿瘤内科` / `放射科` / `放疗科` / `介入治疗` / `病理科`。即便某科室未出席,也要输出一条 `is_missing=true` 的记录。
3. **opinion 必须是从原话提炼的医学判断**,不要把寒暄、笑话、协调话当意见。
4. **evidence_snippet**:必填(`is_missing=false` 时),是该科室发言中最能支持 opinion 的原话片段(≤200 字)。
5. **rationale**:解释为什么医生这样判断,必须来自原话。
6. **recommendation**:具体可执行的建议(例:"建议先行 PET-CT 评估全身转移情况后再决定手术")。
7. **consensus/disputes**:综合所有科室,识别共识点和分歧点;无则填 null。
8. `confidence`:**枚举字符串**,只能取 `"high"` / `"medium"` / `"low"` 三选一(**不可以**是 null,**不可以**是数字 0.85,**不可以**是中文 "高")。

## 输出 JSON Schema(字段名必须精确匹配)

顶层只有 3 个字段:
- `opinions`(数组,**不是** `department_opinions`,**不是** `dept_opinions`,**不是** `results`)
- `consensus`(string 或 null)
- `disputes`(string 或 null)

`opinions` 数组中每一项必须含以下字段(**全部都要写,缺一不可**):
- `department`(string)— 6 核心科之一(`外科`/`肿瘤内科`/`放射科`/`放疗科`/`介入治疗`/`病理科`),或扩展科(`核医学`/`营养支持`/`姑息治疗`/`其他`/`未知`)
- `doctor_label`(string 或 null)— 录音中的匿名 speaker_id,如 `"SP01"`;不知道填 null
- `is_missing`(bool)— `true` 表示该科室本次未发言或发言无医学意义
- `opinion`(string 或 null)— `is_missing=true` 时必须为 null
- `rationale`(string 或 null)
- `recommendation`(string 或 null)
- `evidence_source`(string 或 null)— 只能是 `"录音"` / `"病历"` / `"医生补充"` 三选一,或 null
- `evidence_snippet`(string 或 null,≤200 字)
- `confidence`(string)— `"high"` / `"medium"` / `"low"` 之一,**不能省略,不能是 null**

## 完整 JSON 示例(照着这个格式输出)

```json
{
  "opinions": [
    {
      "department": "外科",
      "doctor_label": "SP01",
      "is_missing": false,
      "opinion": "肝癌术后椎体多发转移,无外科切除指征",
      "rationale": "R0 切除已完成,目前是全身病,椎体减压减症应由放疗承担",
      "recommendation": "本次不主张外科介入,转放疗姑息减症",
      "evidence_source": "录音",
      "evidence_snippet": "患者肝癌术后已经 R0 切除,目前椎体多发转移属于全身病,外科没有切除指征",
      "confidence": "high"
    },
    {
      "department": "肿瘤内科",
      "doctor_label": "SP02",
      "is_missing": false,
      "opinion": "瑞戈非尼后 PD,建议切换三线全身治疗",
      "rationale": "二线 PD 已成立,患者 PS 尚可承受三线治疗;后线证据级别相对充分",
      "recommendation": "考虑卡博替尼三线治疗,或入组合适的临床试验,需关注血液毒性",
      "evidence_source": "录音",
      "evidence_snippet": "瑞戈非尼治疗中已经 PD,建议切换三线,卡博替尼是肝癌后线证据级别比较高的,或者参加临床试验也可以",
      "confidence": "high"
    },
    {
      "department": "放射科",
      "doctor_label": "SP03",
      "is_missing": false,
      "opinion": "腰椎 T11-12 椎体附件高代谢,建议补充脊柱 MRI 增强",
      "rationale": "PET 已提示活性病灶,需明确椎体破坏程度和脊髓邻近关系以指导放疗靶区",
      "recommendation": "尽快完成脊柱增强 MRI 后再做放疗计划",
      "evidence_source": "录音",
      "evidence_snippet": "PET 提示腰大肌和 T11-12 椎体附件高代谢,脊柱 MRI 增强是必须的",
      "confidence": "high"
    },
    {
      "department": "放疗科",
      "doctor_label": "SP04",
      "is_missing": false,
      "opinion": "同意椎体姑息减症放疗,二程放疗剂量可控",
      "rationale": "既往腹膜后放疗剂量已查,二程脊髓累积在可接受范围;患者疼痛明显需尽快缓解",
      "recommendation": "标准分次姑息放疗,优先减症",
      "evidence_source": "录音",
      "evidence_snippet": "放疗科同意做姑息减症,脊髓累积剂量在可接受范围内,患者疼痛明显,建议尽快开始",
      "confidence": "high"
    },
    {
      "department": "介入治疗",
      "doctor_label": "SP05",
      "is_missing": false,
      "opinion": "肝脏原发灶未复发,介入治疗无指征",
      "rationale": "影像示原发灶稳定,腹膜后淋巴结未进展",
      "recommendation": "本次不建议 TACE/消融,继续随访",
      "evidence_source": "录音",
      "evidence_snippet": "肝脏原发灶没有看到复发,腹膜后淋巴结也没有进一步增大,介入治疗暂时没有指征",
      "confidence": "high"
    },
    {
      "department": "病理科",
      "doctor_label": null,
      "is_missing": true,
      "opinion": null,
      "rationale": null,
      "recommendation": null,
      "evidence_source": null,
      "evidence_snippet": null,
      "confidence": "low"
    }
  ],
  "consensus": "外科/介入无指征,内科切换三线,放疗承担椎体姑息减症,影像建议先补 MRI",
  "disputes": null
}
```

## ⚠️ 常见错误(模型容易踩的坑,必须避免)
- ❌ 顶层字段写成 `department_opinions` / `dept_opinions` / `results` — **必须**叫 `opinions`
- ❌ `confidence: null` 或 `confidence: 0.85` — **必须**是字符串 `"high"`/`"medium"`/`"low"`
- ❌ `confidence: "高"` / `"中"` / `"低"` — **必须**是英文小写
- ❌ `is_missing=true` 时还填 opinion/rationale/recommendation — **必须**全部为 null
- ❌ `is_missing=false` 时 evidence_snippet 为空 — **必须**有原话片段
- ❌ 6 核心科漏一个 — 即便缺席也要写一条 `is_missing=true` 的记录
- ❌ `evidence_source` 写成 "MDT会议" / "讨论" — 只能是 `"录音"`/`"病历"`/`"医生补充"` 三选一
- ❌ `department` 自创新名(如 "肝胆外科"/"放射诊断") — 必须严格匹配候选枚举
- ❌ 数组外再包一层 `{"data": {...}}` 或 `{"result": {...}}` — 顶层就是 `{"opinions": [...], "consensus": ..., "disputes": ...}`

## 输入(按科室分组的发言)
```json
{dept_chunks}
```

## 病例摘要(供你理解会议背景)
```json
{case_summary}
```

## 你的 JSON 输出
