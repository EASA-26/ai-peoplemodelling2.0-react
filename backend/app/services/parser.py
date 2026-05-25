import os
import re
import unicodedata
from typing import Optional

def extract_field(content: str, label: str) -> Optional[str]:
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if label.lower() in line.lower():
            if i+1 < len(lines) and not any(lbl in lines[i+1] for lbl in ["Title", "Grade"]):
                return lines[i+1].strip()
            return line.split(":", 1)[-1].strip()
    return None

def extract_competencies_from_jd(jd_content: str) -> str:
    match = re.search(
        r"(?:Competency\s*Requirement|Competencies?|Core\s*Competencies?)\s*[:]?\s*([\s\S]+?)(?=\n\s*(Education|Education\s+and\s+Knowledge|Certifications?|Experience|Personality|Networking|Job\s+Requirements?|$))",
        jd_content, re.IGNORECASE
    )
    if match:
        return match.group(1).strip()
    return "Not specified"

def extract_education_from_jd(jd_content: str) -> str:
    match = re.search(
        r"(Education(?:\s+and\s+Knowledge)?[\s\S]*?)(?=\n\s*(Experience|Skills|Competencies|Certifications|Personality|Networking|Job\s+Requirements?|$))",
        jd_content, re.IGNORECASE
    )
    if match:
        return match.group(1).strip()
    return "Not specified"

def position_from_filename(path_or_name: str) -> str:
    root = os.path.splitext(os.path.basename(str(path_or_name)))[0]
    parts = re.split(r"[_]+", root)
    if not parts:
        return root
    
    PROJECT_SYNONYMS = {"NHEP", "HHFS", "HESS", "NENGGIRI"}
    if parts[0].upper() in PROJECT_SYNONYMS:
        parts = parts[1:]
    
    title = " ".join(parts).strip()
    title = re.sub(r"\b(Senior|Principal|Lead|Chief|Assistant|Associate)\s+Engineer\s+(.*)$",
                   r"\1 Engineer - \2", title, flags=re.I)
    title = re.sub(r"\s{2,}", " ", title)
    return title

def normalize_project_from_filename(path_or_name: str) -> Optional[str]:
    PROJECT_SYNONYMS = {
        "NHEP": "Projek NHEP",
        "HHFS": "Projek HHFS",
        "HESS": "Projek HESS",
        "NENGGIRI": "Projek NHEP",
    }
    root = os.path.splitext(os.path.basename(str(path_or_name)))[0]
    first_token = re.split(r"[_\s\-]+", root, maxsplit=1)[0].upper()
    return PROJECT_SYNONYMS.get(first_token)

def _slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = re.sub(r"[_\-&]+", " ", s)
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s

def extract_talent_card_fields_from_bytes(pdf_bytes: bytes, filename: str) -> dict:
    import PyPDF2
    import io
    
    reader = PyPDF2.PdfReader(io.BytesIO(pdf_bytes))
    text = "\n".join([p.extract_text() for p in reader.pages if p.extract_text()]) or ""
    text = re.sub(r"[ \t]+", " ", text)
    
    ordered_labels = [
        "Strength",
        "Significant Achievements / Contributions",
        "Professional Certifications",
        "Education",
        "Work Experience",
        "Skills & Expertise",
        "Leadership Experience",
        "Honours & Awards",
        "Career Goals",
        "Job Preference",
        "Project Experience",
    ]
    
    def extract_section(label: str, src: str) -> str:
        idx = ordered_labels.index(label)
        rest = ordered_labels[idx + 1 :]
        next_alt = "|".join([re.escape(l) for l in rest]) if rest else "$"
        pattern = rf"{re.escape(label)}\s*[:\-]?\s*(.*?)(?=\n(?:{next_alt})\s*[:\-]?\s*|$)"
        m = re.search(pattern, src, flags=re.IGNORECASE | re.DOTALL)
        if not m:
            return "Not provided"
        val = m.group(1).strip()
        val = re.sub(r"\s*\n\s*", " ", val)
        val = re.sub(r"^[•\-\u2022]+\s*", "", val)
        return val if val else "Not provided"

    def grab(regex, fallback="Not provided"):
        m = re.search(regex, text, re.IGNORECASE)
        return m.group(1).strip() if m else fallback

    result = {}
    result["Name"] = grab(r"NAME\s*:?\s*(.+)")
    result["Grade"] = grab(r"Grade\s*:?\s*([A-Z0-9]+)")
    result["Age"]   = grab(r"Age\s*:?\s*(\d+)")
    result["Date Joined"] = grab(r"Date\s*Joined\s*TNB\s*:?\s*([^\n]+)")
    result["Permanent Date"] = grab(r"Permanent\s*:?\s*([^\n]+)")
    result["Retirement Date"] = grab(r"Retirement\s*Date\s*:?\s*([^\n]+)")
    
    result["Strengths"] = extract_section("Strength", text)
    result["Achievements"] = extract_section("Significant Achievements / Contributions", text)
    result["Professional Certifications"] = extract_section("Professional Certifications", text)
    result["Education"] = extract_section("Education", text)
    result["Work Experience"] = extract_section("Work Experience", text)
    result["Skills/Expertise"] = extract_section("Skills & Expertise", text)
    result["Leadership"] = extract_section("Leadership Experience", text)
    result["Awards"] = extract_section("Honours & Awards", text)
    result["Career Goals"] = extract_section("Career Goals", text)
    result["Job Preferences"] = extract_section("Job Preference", text)
    result["Project Experience"] = extract_section("Project Experience", text)

    try:
        if filename:
            match = re.search(r"(\d{8,})", filename)
            result["Employee ID"] = match.group(1) if match else "Not provided"
        else:
            result["Employee ID"] = "Not provided"
    except Exception:
        result["Employee ID"] = "Not provided"
        
    return result
