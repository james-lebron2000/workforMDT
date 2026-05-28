"""推荐医生/科室的规则库 - 转移部位 → 专科映射。

参考用户方案 §9 的推荐逻辑:
- 腹膜转移 → 腹膜转移/结直肠外科专家
- 肝转移 → 肝胆外科/结直肠肝转移 MDT
- 肺转移 → 胸外科/结直肠肿瘤内科
- NET → 神经内分泌肿瘤专病门诊
- 局部复发 → 盆腔复发 MDT
- 晚期多线 → 肿瘤内科/临床试验门诊
- 疼痛明显 → 姑息治疗/疼痛科
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass
class ReferralRule:
    triggers: List[str]  # 任一关键词命中
    dept: str
    doctor_hint: str
    reason: str
    priority: str = "中"  # 高/中/低
    bring_with: List[str] = None  # type: ignore

    def __post_init__(self):
        if self.bring_with is None:
            self.bring_with = ["病理报告", "影像光盘", "既往治疗记录"]


RULES: List[ReferralRule] = [
    ReferralRule(
        triggers=["腹膜转移", "腹膜种植", "腹腔种植"],
        dept="腹膜转移 MDT / 结直肠外科",
        doctor_hint="腹膜转移亚专科或 HIPEC 治疗中心",
        reason="腹膜转移管理需多学科评估,部分中心可行 CRS + HIPEC",
        priority="高",
        bring_with=["病理报告", "影像光盘", "腹腔镜探查记录", "既往治疗记录"],
    ),
    ReferralRule(
        triggers=["肝转移"],
        dept="结直肠肝转移 MDT / 肝胆外科",
        doctor_hint="结直肠肝转移亚专科教授",
        reason="肝转移病灶可切除性评估需肝胆外科+内科联合判断,部分可行转化手术",
        priority="高",
        bring_with=["病理报告", "肝脏增强 MRI/CT", "肿瘤标志物动态值", "既往治疗记录"],
    ),
    ReferralRule(
        triggers=["肺转移"],
        dept="结直肠肿瘤内科 / 胸外科",
        doctor_hint="结直肠肿瘤内科教授(肺转移寡转移可考虑胸外科联合)",
        reason="肺多发转移以全身治疗为主;寡转移可评估局部治疗机会",
        priority="高",
        bring_with=["病理报告", "胸部增强 CT", "既往治疗记录"],
    ),
    ReferralRule(
        triggers=["脑转移"],
        dept="神经外科 / 放疗科",
        doctor_hint="脑转移亚专科或立体定向放疗中心",
        reason="脑转移需评估手术、SBRT/SRS 等局部治疗选择",
        priority="高",
        bring_with=["脑 MRI 增强", "病理报告", "既往治疗记录"],
    ),
    ReferralRule(
        triggers=["骨转移"],
        dept="骨肿瘤科 / 放疗科",
        doctor_hint="骨转移亚专科(评估姑息放疗、骨改良药物)",
        reason="骨转移以姑息放疗+骨改良药物为主,评估骨相关事件风险",
        priority="中",
    ),
    ReferralRule(
        triggers=["神经内分泌", "NET", "类癌"],
        dept="神经内分泌肿瘤专病门诊",
        doctor_hint="NET 专病门诊或核医学",
        reason="神经内分泌肿瘤治疗策略与一般实体瘤显著不同,需专病管理",
        priority="高",
    ),
    ReferralRule(
        triggers=["局部复发", "吻合口复发", "盆腔复发"],
        dept="盆腔复发 MDT",
        doctor_hint="盆腔复发亚专科外科+放疗联合门诊",
        reason="局部复发需评估再切除/再放疗可行性,处理复杂",
        priority="高",
    ),
    ReferralRule(
        triggers=["三线", "四线", "多线治疗", "末线"],
        dept="肿瘤内科 / 临床试验门诊",
        doctor_hint="临床试验中心或药物早期临床",
        reason="多线治疗后建议评估临床试验机会",
        priority="中",
        bring_with=["完整治疗记录", "近期影像", "基因检测报告", "病理 IHC 报告"],
    ),
    ReferralRule(
        triggers=["疼痛", "重度疼痛", "VAS"],
        dept="姑息治疗 / 疼痛科",
        doctor_hint="姑息治疗或疼痛门诊",
        reason="疼痛管理及生活质量优化",
        priority="中",
    ),
]


def match_referrals(text: str) -> List[ReferralRule]:
    """根据文本(临床判断/病情综述)命中所有规则。"""
    hits: List[ReferralRule] = []
    seen_depts = set()
    for rule in RULES:
        for trigger in rule.triggers:
            if trigger in text:
                if rule.dept not in seen_depts:
                    hits.append(rule)
                    seen_depts.add(rule.dept)
                break
    return hits
