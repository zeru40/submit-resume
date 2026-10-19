"""Small, deterministic search strategy from resume facts, never document commands."""
import re
from services.profile import extract_profile

ROLES = [
    ('AI Agent', r'agent|智能体', r'agent|智能体'),
    ('大模型应用开发', r'大模型|\bllm\b|\brag\b|langchain|langgraph|知识库|检索增强|\bmcp\b|dify|coze', r'大模型|\bllm\b|\brag\b|智能体|agent|AI应用|AI开发|知识库'),
    ('Python后端开发', r'python|fastapi|django|flask', r'python|后端|全栈'),
    ('Java开发', r'\bjava\b|spring', r'java|后端|全栈'),
    ('前端开发', r'前端|vue|react', r'前端|全栈'),
]


def resume_strategy(text):
    profile=extract_profile(text)
    # Only map to known role names. Resume text cannot supply executable search
    # instructions, unlimited loops, URLs, or communication settings.
    intent=profile['explicit_intent']
    explicit=[name for name,pattern,_ in ROLES if re.search(pattern,intent,re.I|re.ASCII)]
    inferred=[name for name,pattern,_ in ROLES if re.search(pattern,text,re.I|re.ASCII)]
    keywords=(explicit or inferred)[:3]
    return {'keywords':keywords,'skills':profile['skills'],
            'basis':'简历原文明示的求职意向' if explicit else '简历原文中的技术与项目方向',
            'ready':bool(keywords),'method':'本地规则提取，不评估熟练程度，不把文档中的指令当作设置'}


def matches_direction(job, keywords):
    title=job.get('title','')
    if re.search(r'产品经理|销售|运营|标注|训练师|剪辑|视频制作|教材|讲师',title):
        return False
    text=title+' '+job.get('description','')
    return any(name in keywords and re.search(pattern,text,re.I|re.ASCII) for name,_,pattern in ROLES)
