import re
from typing import Dict, Any, List

def education_to_buckets(education_text: str) -> Dict[str, bool]:
    from .parser import _slug
    t = _slug(education_text)
    
    ENGINEERING_FAMILY = {
        "engineering", "engineer", "engg",
        "civil", "electrical", "mechanical", "mechatronic", "mechatronics",
        "electronic", "electronics", "instrument", "instrumentation", "control",
        "c&i", "automation", "chemical", "petroleum", "aerospace",
        "industrial", "manufacturing", "materials", "marine", "naval", "power",
        "telecommunication", "telecommunications", "computer engineering"
    }
    CIVIL_KEYS = {"civil", "structural"}
    ELECTRICAL_KEYS = {"electrical", "power", "telecommunication", "telecommunications"}
    MECHANICAL_KEYS = {"mechanical", "mechatronic", "mechatronics", "industrial", "manufacturing"}
    ELECTRONIC_KEYS = {"electronic", "electronics", "instrument", "instrumentation", "control", "c&i", "automation"}
    LAW_FAMILY = {"law", "legal", "llb", "juris", "jurisprudence", "syariah", "sharia"}
    FINANCE_FAMILY = {"finance", "financial", "accounting", "accountancy", "accountant", "acca", "cpa", "cfa", "economics", "banking"}

    has_any_engineering = any(k in t for k in ENGINEERING_FAMILY)
    has_civil = any(k in t for k in CIVIL_KEYS)
    has_electrical = any(k in t for k in ELECTRICAL_KEYS)
    has_mechanical = any(k in t for k in MECHANICAL_KEYS)
    has_electronic = any(k in t for k in ELECTRONIC_KEYS)
    has_law = any(k in t for k in LAW_FAMILY)
    has_finance = any(k in t for k in FINANCE_FAMILY)
    
    return {
        "any_engineering": has_any_engineering,
        "civil": has_civil,
        "electrical": has_electrical,
        "mechanical": has_mechanical,
        "electronic": has_electronic,
        "law": has_law,
        "finance": has_finance,
    }

def jd_required_disciplines(jd_text: str) -> Dict[str, bool]:
    from .parser import _slug
    t = _slug(jd_text)
    
    LAW_FAMILY = {"law", "legal", "llb", "juris", "jurisprudence", "syariah", "sharia"}
    FINANCE_FAMILY = {"finance", "financial", "accounting", "accountancy", "accountant", "acca", "cpa", "cfa", "economics", "banking"}

    allow_law = any(k in t for k in LAW_FAMILY)
    allow_finance = any(k in t for k in FINANCE_FAMILY)
    civil_only = bool(re.search(r"\b(civil)\s+engineering\b", t))
    electrical_only = bool(re.search(r"\b(electrical|power)\s+engineering\b", t))
    mechanical_only = bool(re.search(r"\b(mechanical|mechatronic[s]?)\s+engineering\b", t))
    electronic_only = bool(re.search(r"\b(electronic[s]?|instrument(ation)?|control)\s+engineering\b", t))
    any_engineering = "engineering" in t or "engineer" in t
    narrow_selected = civil_only or electrical_only or mechanical_only or electronic_only
    
    return {
        "any_engineering": any_engineering and not narrow_selected,
        "civil_only": civil_only,
        "electrical_only": electrical_only,
        "mechanical_only": mechanical_only,
        "electronic_only": electronic_only,
        "allow_law": allow_law,
        "allow_finance": allow_finance,
    }

def matches_requirement(candidate_buckets: Dict[str,bool], rule: Dict[str,bool]) -> bool:
    if rule["allow_law"] and candidate_buckets["law"]:
        return True
    if rule["allow_finance"] and candidate_buckets["finance"]:
        return True
    if rule["any_engineering"] and candidate_buckets["any_engineering"]:
        return True
    if rule["civil_only"] and candidate_buckets["civil"]:
        return True
    if rule["electrical_only"] and candidate_buckets["electrical"]:
        return True
    if rule["mechanical_only"] and candidate_buckets["mechanical"]:
        return True
    if rule["electronic_only"] and candidate_buckets["electronic"]:
        return True
    return False

def grade_matches(candidate_grade: str, selected_grade: str) -> bool:
    def norm(s: str) -> str:
        return re.sub(r"\s+", "", str(s or "").upper())
    def extract_grade_tokens(s: str):
        s = norm(s)
        toks = re.findall(r'(?:CM|GM|M|E)\d{2}', s)
        return toks or ([s] if s else [])
        
    sel = norm(selected_grade)
    mapping = {
        "M16": {"M16", "CM16", "GM16", "M15", "CM15", "GM15"},
        "M15": {"M15", "CM15", "GM15", "E17", "E16"},
        "E16": {"E17", "E16", "E15", "E14"},
        "E17": {"E17", "E16", "E15", "E14"},
        "E16&E17": {"E17", "E16", "E15", "E14"},
        "E14": {"E15", "E14", "E13", "E12"},
        "E15": {"E15", "E14", "E13", "E12"},
        "E14&E15": {"E15", "E14", "E13", "E12"},
        "E12": {"E12", "E13"},
        "E13": {"E12", "E13"},
        "E12&E13": {"E12", "E13"},
    }
    
    if sel not in mapping:
        compact = sel.replace(" ", "")
        if compact in {"E16E17", "E14E15", "E12E13"}:
            sel = compact[:3] + "&" + compact[3:]
            
    if sel not in mapping:
        jd_range = re.findall(r"E\d+", sel)
        if not jd_range:
            return False
        if len(jd_range) == 1:
            wanted = jd_range[0]
            cand_tokens = extract_grade_tokens(candidate_grade)
            return any(tok == wanted for tok in cand_tokens)
        else:
            try:
                start = int(jd_range[0][1:])
                end = int(jd_range[1][1:])
                cand_tokens = extract_grade_tokens(candidate_grade)
                for tok in cand_tokens:
                    m = re.match(r"E(\d{2})$", tok)
                    if m:
                        val = int(m.group(1))
                        if start <= val <= end:
                            return True
                return False
            except Exception:
                return False
                
    allowed = mapping[sel]
    cand_tokens = extract_grade_tokens(candidate_grade)
    return any(tok in allowed for tok in cand_tokens)

def compute_succession_score(row: Dict[str, Any]) -> int:
    import re
    from datetime import datetime
    
    score = 0
    def norm(val): return str(val).strip().lower() if val else ""
    
    try:
        if isinstance(row.get("KPI"), str):
            numbers = re.findall(r"(\d{4}):\s*([\d.]+)", row["KPI"])
            kpis = [float(v) for _, v in numbers]
            if kpis and sum(kpis) / len(kpis) >= 80:
                score += 3
        elif float(row.get("KPI", 0)) >= 80:
            score += 3
    except:
        pass
        
    if norm(row.get("Critical Role (Yes/No)")) == "yes":
        score += 1
        
    years_exp = None
    if "Years of Experience" in row and row["Years of Experience"] not in [None, "", "Not provided"]:
        try:
            years_exp = float(row["Years of Experience"])
        except:
            years_exp = None
    elif "Date Hired" in row and row["Date Hired"] not in [None, "", "Not provided"]:
        try:
            hired_year = int(str(row["Date Hired"])[:4])
            years_exp = datetime.now().year - hired_year
        except:
            years_exp = None
            
    if years_exp is not None:
        if years_exp > 10:
            score += 3
        elif years_exp > 5:
            score += 2
            
    return score
