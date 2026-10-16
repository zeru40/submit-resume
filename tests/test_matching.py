from services.matching import match_job,salary_range

def sample(**extra):
    return dict(title='Python 后端',company='公司',salary='15-25K',location='上海·浦东',description='Python FastAPI 系统开发。负责系统架构、接口开发、数据库设计和业务交付。',**extra)

def test_skills_require_resume_evidence():
    score,_,eligible=match_job(sample(),{'skills':'Python,FastAPI','city':'上海','min_score':80},'Python 工程师')
    assert score==70
    assert not eligible

def test_missing_salary_never_qualifies():
    job=sample();job['salary']='面议'
    _,reasons,eligible=match_job(job,{'salary_min':15},'')
    assert not eligible and '薪资待确认' in reasons

def test_exclusions_override_high_score():
    job=sample();job['company']='某外包公司'
    score,_,eligible=match_job(job,{'exclude_keywords':'外包','skills':'Python'},'Python')
    assert score==0 and not eligible

def test_salary_range():
    assert salary_range('15-25K·14薪')==(15,25)
    assert salary_range('1.5-2万')==(15,20)

def test_incomplete_detail_and_city_require_review():
    job=sample();job['description']='Python';job['location']=''
    _,reasons,eligible=match_job(job,{'city':'上海'},'Python')
    assert not eligible and len(reasons)>=2
