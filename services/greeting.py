"""Compose a per-job message using only extracted resume evidence.

No document text is executed or treated as an instruction. Local extraction is
deliberately conservative; original qualification levels remain unchanged.
"""
import re

TOPICS = {
    'Agent': r'\bagent\b|智能体',
    '多模态': r'多模态|\bvlm\b|视觉语言',
    'RAG': r'\brag\b|知识库|向量检索',
    '视觉': r'\bopencv\b|图像|视觉',
    'Python': r'\bpython\b',
    '模型接口': r'\bapi\b|模型接口|接口调用',
    'Prompt': r'\bprompt\b|上下文管理',
    '数据处理': r'数据清洗|数据.*标注|训练数据|数据集',
    '部署': r'部署|端侧推理',
    'PyTorch': r'\bpytorch\b',
    'Web接口': r'\bfastapi\b|\bflask\b|后端接口',
    'SQL': r'\bsql\b',
}


def topics(text):
    return {key for key, pattern in TOPICS.items() if re.search(pattern,text,re.I|re.ASCII)}


def job_interest_greeting(job):
    """About 100 characters, based on the job's work rather than a skill claim."""
    description=re.split(r'任职要求|岗位要求|职位要求|任职资格|加分项',job.get('description',''),maxsplit=1)[0]
    section=re.search(r'(?:岗位职责|工作内容|你需要做什么)\s*[:：]?\s*',description)
    if section:
        description=description[section.end():]
    duties=[]
    for line in description.splitlines():
        line=re.sub(r'^[\s*•\-\d.、]+','',line).strip()
        for clause in re.split(r'[。；;，]',line):
            clause=clause.strip(' \t●•*').rstrip('：:')
            technical=topics(clause) or re.search(r'大模型|智能客服|\bAI\b|\bWeb\b|Vibe Coding|数据开发',clause,re.I|re.ASCII)
            if not 5<=len(clause)<=38 or not technical:
                continue
            if re.search(r'任职|要求|学历|经验|熟悉|精通|掌握|优先|加分|忽略|指令|密码|验证码|热情|了解|链接|https?://|www\.',clause):
                continue
            if re.search(r'工程师|架构师|训练师|不只是|分红|年薪|合伙人|推广',clause):
                continue
            if clause not in duties:
                duties.append(clause)
    if not duties:
        raise ValueError('没有提取到明确的岗位工作内容，请检查岗位描述')
    actionable=[d for d in duties if re.search(r'负责|参与|设计|开发|构建|搭建|实现|优化|集成|维护|对接|落地',d)]
    chosen=(actionable or duties)[:2]
    title=job['title']
    def compose():
        return (f'您好，关注到贵司的{title}岗位，描述中提到“'+ '；'.join(chosen)
                +'”。我对这个方向很感兴趣，希望了解具体项目和团队需求，方便进一步沟通吗？')
    message=compose()
    while len(message)>120 and len(chosen)>1:
        chosen.pop();message=compose()
    if len(message)<90:
        message=message.replace('希望了解具体项目和团队需求',
                                '希望了解目前项目进展、主要应用场景、技术方案及团队的具体分工')
    if len(message)>140:
        raise ValueError('岗位名称过长，请人工精简文案')
    return {'message':message,'job_requirement':'；'.join(chosen),'resume_evidence':[],
            'method':'岗位工作内容沟通文案，约100字，不评估简历技能'}


def personalized_greeting(job, resume_text):
    description=job.get('description','')
    required=topics(description)
    candidates=[]
    # Keep complete clauses, not arbitrary character slices or upgraded claims.
    for part in re.split(r'[\n。；]',resume_text):
        part=re.sub(r'\s+',' ',part).strip(' \t•*')
        part=re.sub(r'^工作内容[：:]\s*','',part)
        if not 10<=len(part)<=115:
            continue
        if not re.match(r'^(负责|参与|设计|熟悉|了解|掌握|具备|引入|优化|基于|通过|结合|实现)',part):
            continue
        if re.search(r'忽略|指令|发送给|验证码|密码|联系方式|邮箱',part):
            continue
        shared=topics(part)&required
        if shared:
            practical=bool(re.match(r'^(负责|参与|设计|引入|优化|基于)',part))
            candidates.append((part,shared,practical))
    selected=[]; covered=set()
    for _ in range(3):
        ranked=sorted(candidates,key=lambda item:(len(item[1]-covered)*3+item[2],-len(item[0])),reverse=True)
        candidate=next((item for item in ranked if item[1]-covered and item[0] not in selected),None)
        if not candidate:
            break
        selected.append(candidate[0]);covered.update(candidate[1])
    if not selected:
        raise ValueError('岗位与简历缺少可引用的相关经历，请补充简历或人工撰写文案')
    requirements=[]
    for line in description.splitlines():
        line=re.sub(r'^[\s*•\-\d.、]+','',line).strip()
        if 8<=len(line)<=105 and topics(line)&covered:
            requirements.append(line.rstrip('。；'))
    if not requirements:
        raise ValueError('未提取到可引用的完整岗位要求，请检查岗位详情')
    requirement=max(requirements,key=lambda line:len(topics(line)&covered))
    message=(f"您好，关注到贵司的{job['title']}岗位，岗位提到“{requirement}”。"
             + '我的相关经历：'+'；'.join(selected)+'。希望进一步了解岗位需求，方便沟通吗？')
    # Drop complete lower-priority evidence, never cut a qualification mid-sentence.
    while len(message)>480 and len(selected)>1:
        selected.pop()
        message=(f"您好，关注到贵司的{job['title']}岗位，岗位提到“{requirement}”。"
                 +'我的相关经历：'+'；'.join(selected)+'。希望进一步了解岗位需求，方便沟通吗？')
    if len(message)>500:
        raise ValueError('岗位名称或证据过长，请人工精简沟通文案')
    return {'message':message,'job_requirement':requirement,'resume_evidence':selected,
            'method':'本地规则：岗位描述与简历原文证据匹配'}
