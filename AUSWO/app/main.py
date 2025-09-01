from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, Literal
import json, pathlib

# 1) 加载“写死”的规则（服务启动时一次性读入内存）
RULES_PATH = pathlib.Path(__file__).parent / "rules.json"
RULES = json.loads(RULES_PATH.read_text(encoding="utf-8"))

# 2) 请求模型（前端按此发 JSON）
class English(BaseModel):
    # 两种考试任一：
    test: Literal["ielts", "pte"]
    # overall 与 四科任选其一；都给也行（优先以 overall 判）
    overall: Optional[float] = None
    listening: Optional[float] = None
    reading: Optional[float] = None
    writing: Optional[float] = None
    speaking: Optional[float] = None

class WorkExp(BaseModel):
    overseas_years: int = Field(0, ge=0)
    aus_years: int = Field(0, ge=0)

class AuStudy(BaseModel):
    completed: bool = False
    regional: bool = False

class CalcRequest(BaseModel):
    visa: Literal["189", "190", "491"] = "189"
    age: int = Field(ge=0, le=100)
    english: English
    education: Literal["phd","master","bachelor","diploma","trade"]
    work_experience: WorkExp
    australia_study: AuStudy = AuStudy()
    professional_year: bool = False
    naati: bool = False
    partner: Literal["single","skilled","english_only","none"] = "none"

# 3) FastAPI 应用与 CORS（方便本地或前端域名调用）
app = FastAPI(title="AUSWO Calculator API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],           # 生产请改成你的前端域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/healthz")
def health():
    return {"ok": True, "rules_version": RULES["meta"]["version"], "updated_at": RULES["meta"]["updated_at"]}

# 4) —— 纯函数：打分工具 —— #
def _bucket_points(buckets, value: float) -> int:
    """区间打分：给定 buckets=[{min,max,points},...]，返回匹配到的 points。"""
    for b in buckets:
        if value >= b["min"] and value <= b.get("max", float("inf")):
            return b["points"]
    return 0

def score_age(age: int) -> int:
    return _bucket_points(RULES["age"], age)

def score_english(eng: English) -> int:
    e = eng.model_dump()
    test = e["test"]
    # 先看 overall
    if e.get("overall") is not None:
        ov = float(e["overall"])
        if test == "ielts":
            return _bucket_points(RULES["english"]["ielts_overall"], ov)
        else:  # pte
            return _bucket_points(RULES["english"]["pte_overall"], ov)
    # 没有 overall，就用四科的“最小值”判档
    bands = [e.get("listening"), e.get("reading"), e.get("writing"), e.get("speaking")]
    if all(v is not None for v in bands):
        mn = float(min(bands))  # 以最低分判断
        if test == "ielts":
            # ielts_bands 只有 min 门槛；>=8.0 取 20，否则 >=7.0 取 10
            # 从高到低判断更直观
            for row in sorted(RULES["english"]["ielts_bands"], key=lambda x: x["min"], reverse=True):
                if mn >= row["min"]:
                    return row["points"]
        else:
            for row in sorted(RULES["english"]["pte_bands"], key=lambda x: x["min"], reverse=True):
                if mn >= row["min"]:
                    return row["points"]
    return 0

def score_education(level: str) -> int:
    return RULES["education"]["mapping"].get(level, 0)

def score_experience(exp: WorkExp) -> int:
    r = RULES["work_experience"]
    over = _bucket_points(r["overseas"], exp.overseas_years)
    aus  = _bucket_points(r["australia"], exp.aus_years)
    if r["mode"] == "sum_cap":
        return min(r["cap_points"], over + aus)
    elif r["mode"] == "max_only":
        return max(over, aus)
    return 0

def score_au_study(stu: AuStudy) -> int:
    pts = 0
    if stu.completed:
        pts += RULES["australia_study"]["points"]
        if stu.regional:
            pts += RULES["australia_study"]["regional_bonus"]
    return pts

def score_optional(professional_year: bool, naati: bool, partner: str) -> dict:
    return {
        "professional_year": RULES["professional_year"]["points"] if professional_year else 0,
        "naati": RULES["naati"]["points"] if naati else 0,
        "partner": RULES["partner"].get(partner, 0)
    }

def score_state_nomination(visa: str) -> int:
    # 189 → 0；190/491 → 表中加分
    return RULES["state_nomination"].get(visa, 0)

# 5) —— API 路由 —— #
@app.post("/points/calc")
def calc_points(req: CalcRequest):
    try:
        br = {}
        br["age"]              = score_age(req.age)
        br["english"]          = score_english(req.english)
        br["education"]        = score_education(req.education)
        br["work_experience"]  = score_experience(req.work_experience)
        br["australia_study"]  = score_au_study(req.australia_study)

        opt = score_optional(req.professional_year, req.naati, req.partner)
        br.update(opt)

        br["state_nomination"] = score_state_nomination(req.visa)

        total = sum(br.values())

        return {
            "visa": req.visa,
            "total": total,
            "breakdown": br,
            "notes": [
                f"Rules {RULES['meta']['version']} ({RULES['meta']['updated_at']})",
                "Demo only. Verify with current DHA policy if used in production."
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
