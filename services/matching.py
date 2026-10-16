"""Deterministic filtering. Missing facts never become claimed qualifications."""
import re

def terms(value):
    return [x.strip() for x in re.split(r'[,，;；\n]', value or '') if x.strip()]

def salary_range(text):
    s = (text or '').lower().replace(' ', '').replace('千', 'k')
    match = re.search(r'(\d+(?:\.\d+)?)\s*[-–~至]\s*(\d+(?:\.\d+)?)([kw万])', s)
    if match:
        lo, hi = float(match[1]), float(match[2])
        scale = 10 if match[3] in {'w', '万'} else 1
        return lo * scale, hi * scale
    return None

def match_job(job, plan, resume_text):
    text = ' '.join(str(job.get(k, '')) for k in ('title','company','location','salary','description'))
    folded = text.casefold()
    reasons, blocked, unknown = [], False, False
    for term in terms(plan.get('exclude_keywords', '')):
        if term.casefold() in folded:
            reasons.append('命中排除条件：' + term)
            blocked = True
    city = plan.get('city', '').strip()
    if city:
        if not job.get('location'):
            reasons.append('工作城市待确认'); unknown = True
        elif city not in job['location']:
            reasons.append('工作城市不符合'); blocked = True
    minimum = plan.get('salary_min', 0)
    if minimum:
        salary = salary_range(job.get('salary', ''))
        if not salary:
            reasons.append('薪资待确认'); unknown = True
        elif salary[1] < minimum:
            reasons.append('薪资上限低于期望'); blocked = True
        else:
            reasons.append('薪资区间与期望有交集')
    skills = [] if plan.get('greeting_style')=='job_interest' else terms(plan.get('skills', ''))
    evidence = (resume_text or '').casefold()
    hits = [s for s in skills if s.casefold() in folded and s.casefold() in evidence]
    score = round(40 + 60 * len(hits) / max(1, len(skills))) if skills else 60
    if hits:
        reasons.append('简历与岗位共同技能：' + '、'.join(hits))
    if not skills:
        reasons.append('未配置技能，按基础规则排序')
    if len(job.get('description', '')) < 30:
        reasons.append('岗位详情不完整，需人工查看'); unknown = True
    if blocked:
        score = 0
    eligible = not blocked and not unknown and score >= plan.get('min_score', 60)
    return score, reasons, eligible
