"""医学术语字典 + OCR/ASR 编辑距离纠错。

迁移自 shan-ye terminology.py 思路,聚焦肿瘤 MDT 高频词:
- 化疗药物 / 靶向药 / 免疫药
- 分子标志物 (PD-L1/MSI/RAS/BRAF/HER2)
- 解剖学术语
- 检查项目
"""
from __future__ import annotations

from functools import lru_cache
from typing import List, Optional, Tuple

try:
    from rapidfuzz import fuzz, process
except ImportError:  # 允许在不装 rapidfuzz 时也可 import
    process = None  # type: ignore
    fuzz = None  # type: ignore


# 核心术语表(MVP 起步,后续可扩到 5000+)。
# 按类别组织以便未来按类约束纠错。
TERMS_BY_CATEGORY: dict[str, list[str]] = {
    "化疗药物": [
        "奥沙利铂", "卡铂", "顺铂", "紫杉醇", "多西他赛", "白蛋白紫杉醇",
        "氟尿嘧啶", "5-FU", "卡培他滨", "希罗达",
        "伊立替康", "雷替曲塞", "吉西他滨", "替吉奥",
        "FOLFOX", "FOLFIRI", "FOLFOXIRI", "XELOX", "CAPOX",
        "依托泊苷", "环磷酰胺", "多柔比星", "表柔比星",
    ],
    "靶向药物": [
        "贝伐珠单抗", "西妥昔单抗", "帕尼单抗", "雷莫芦单抗",
        "瑞戈非尼", "呋喹替尼", "阿帕替尼", "安罗替尼",
        "曲妥珠单抗", "帕妥珠单抗", "T-DM1", "T-DXd",
        "奥希替尼", "吉非替尼", "厄洛替尼", "阿法替尼", "克唑替尼", "阿来替尼",
        "拉罗替尼", "恩曲替尼", "拉帕替尼",
    ],
    "免疫药物": [
        "帕博利珠单抗", "纳武利尤单抗", "信迪利单抗", "替雷利珠单抗",
        "卡瑞利珠单抗", "特瑞普利单抗", "度伐利尤单抗", "阿替利珠单抗",
        "伊匹木单抗", "PD-1", "PD-L1", "CTLA-4",
    ],
    "分子标志物": [
        "MSI-H", "MSS", "MSI-L", "dMMR", "pMMR",
        "RAS", "KRAS", "NRAS", "HRAS",
        "BRAF", "V600E",
        "HER2", "ERBB2",
        "NTRK", "ROS1", "ALK", "RET", "MET",
        "EGFR", "TP53", "PIK3CA", "PTEN",
        "TMB", "肿瘤突变负荷",
    ],
    "影像术语": [
        "增强 CT", "平扫 CT", "MRI", "PET-CT", "PET/CT", "PET-MR",
        "DCE-MRI", "DWI", "T1WI", "T2WI",
        "肝转移", "肺转移", "骨转移", "脑转移", "腹膜转移", "淋巴结转移",
        "原发灶", "转移灶", "靶病灶", "非靶病灶",
        "RECIST", "iRECIST", "PR", "CR", "SD", "PD",
    ],
    "手术术语": [
        "R0 切除", "R1 切除", "R2 切除",
        "根治性切除", "姑息性切除", "减瘤手术", "转化手术",
        "腹腔镜", "机器人辅助", "经肛", "全直肠系膜切除", "TME",
        "区域淋巴结清扫", "前哨淋巴结",
    ],
    "病理术语": [
        "中分化", "高分化", "低分化", "未分化",
        "腺癌", "鳞癌", "腺鳞癌", "神经内分泌癌", "小细胞癌", "未分化癌",
        "导管原位癌", "浸润性癌", "黏液腺癌", "印戒细胞癌",
        "脉管侵犯", "神经侵犯", "切缘阴性", "切缘阳性",
        "免疫组化", "IHC", "FISH",
    ],
    "化验项目": [
        "白细胞", "中性粒细胞", "血红蛋白", "血小板",
        "AST", "ALT", "TBIL", "ALB", "BUN", "Cr",
        "CEA", "CA199", "CA125", "CA153", "AFP", "PSA", "CA724",
        "NSE", "ProGRP", "SCC",
        "LDH", "ALP",
    ],
    "治疗方式": [
        "化疗", "放疗", "免疫治疗", "靶向治疗", "内分泌治疗", "中医治疗",
        "TACE", "肝动脉化疗栓塞", "射频消融", "微波消融", "冷冻消融",
        "立体定向放疗", "SBRT", "IMRT", "VMAT", "粒子植入",
        "新辅助治疗", "辅助治疗", "转化治疗", "姑息治疗",
        "ECOG", "KPS",
    ],
}

# 构建扁平词表(去重)
ALL_TERMS: list[str] = sorted(
    {term for terms in TERMS_BY_CATEGORY.values() for term in terms}
)


@lru_cache(maxsize=1)
def _term_set() -> frozenset:
    return frozenset(ALL_TERMS)


def correct_term(
    candidate: str,
    threshold: int = 88,
    category: Optional[str] = None,
) -> Optional[Tuple[str, int]]:
    """对 OCR/ASR 错字做编辑距离纠错。

    Args:
        candidate: 待纠错的词
        threshold: rapidfuzz 分数阈值(0-100)
        category: 限定在某类别(可选)
    Returns:
        (纠正词, 分数) 或 None
    """
    if not candidate or process is None:
        return None
    terms = TERMS_BY_CATEGORY[category] if category else ALL_TERMS
    if not terms:
        return None
    match = process.extractOne(candidate, terms, scorer=fuzz.ratio)
    if match is None:
        return None
    matched_term, score, _ = match
    if score >= threshold and matched_term != candidate:
        return matched_term, int(score)
    return None


def has_known_term(text: str, terms: Optional[List[str]] = None) -> bool:
    """快速判断文本是否包含已知医学术语 - 用于科室归类的弱信号。"""
    pool = terms or ALL_TERMS
    return any(t in text for t in pool)


# 科室关键词索引(给 LLM 兜底的弱归类信号)
DEPT_KEYWORDS: dict[str, list[str]] = {
    "外科": ["R0", "R0切除", "TME", "根治性切除", "腹腔镜", "淋巴结清扫", "手术机会", "可切除"],
    "肿瘤内科": ["化疗", "FOLFOX", "XELOX", "靶向", "贝伐", "西妥昔", "PD-1", "免疫治疗", "二线", "三线"],
    "放射科": ["CT", "MRI", "PET", "影像", "病灶", "DWI", "增强", "扫描"],
    "放疗科": ["放疗", "SBRT", "IMRT", "VMAT", "立体定向", "粒子", "剂量", "Gy"],
    "介入治疗": ["TACE", "栓塞", "射频", "消融", "微波", "冷冻", "粒子植入", "穿刺"],
    "病理科": ["分化", "腺癌", "鳞癌", "免疫组化", "IHC", "脉管侵犯", "切缘"],
}


def hint_department(text: str) -> Optional[str]:
    """根据关键词命中数,弱推测一段发言属于哪个科室。命中并列时返回 None。"""
    scores: dict[str, int] = {}
    for dept, keywords in DEPT_KEYWORDS.items():
        scores[dept] = sum(1 for k in keywords if k in text)
    if not scores:
        return None
    sorted_scores = sorted(scores.items(), key=lambda x: -x[1])
    if sorted_scores[0][1] == 0:
        return None
    if len(sorted_scores) > 1 and sorted_scores[0][1] == sorted_scores[1][1]:
        return None  # 并列,不下结论
    return sorted_scores[0][0]
