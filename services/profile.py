"""Extract only explicit resume facts; document text is data, never instructions."""
import re

SKILLS = {
    'Python':r'\bpython\b', 'RAG':r'\brag\b', 'AI Agent':r'\bai\s*agent\b',
    'FAISS':r'\bfaiss\b', 'Chroma':r'\bchroma\b', 'Milvus':r'\bmilvus\b',
    'OpenCV':r'\bopencv\b', 'PyTorch':r'\bpytorch\b', 'TensorFlow':r'\btensorflow\b',
    'FreeRTOS':r'\bfreertos\b', 'MQTT':r'\bmqtt\b', 'SQL':r'\bsql\b',
    'FastAPI':r'\bfastapi\b', 'Vue':r'\bvue\b', 'React':r'\breact\b',
    'Java':r'\bjava\b', 'C#':r'\bc#', 'C++':r'\bc\+\+',
    'Prompt Engineering':r'\bprompt\s+engineering\b', 'Embedding':r'\bembedding\b',
}

def extract_profile(text):
    name = re.search(r'姓\s*名\s*[:：]\s*([^\s\n]{2,20})',text)
    city = re.search(r'(?:所在城市|现居住地|现居城市|现居地|居住城市)\s*[:：]\s*([^\n\r]{2,45})',text)
    intent = re.search(r'(?:求职意向|意向岗位|期望职位)\s*[:：]\s*([^\n\r]{2,80})',text)
    education = re.search(r'(?:学历|学位)\s*[:：]\s*(博士|硕士|本科|大专|中专|高中)',text)
    if not education:
        education = re.search(r'[^\n]*(?:大学|学院|学校)[^\n]*?(博士|硕士|本科|大专|中专|高中)',text)
    return {'name':name[1] if name else '', 'current_city':city[1].strip() if city else '',
            'education':education[1] if education else '', 'explicit_intent':intent[1].strip() if intent else '',
            'skills':[name for name,pattern in SKILLS.items() if re.search(pattern,text,re.I | re.ASCII)],
            'characters':len(text), 'method':'本地文字解析；仅识别原文出现的信息'}
