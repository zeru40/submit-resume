"""Applicant-side selectors. Keep site changes out of task orchestration.

Selectors are candidates, not proof of success. An ambiguous/duplicate match
must pause rather than click an arbitrary element.
"""
SPECS = {
    'boss': {
        'label': 'BOSS直聘', 'home': 'https://www.zhipin.com/web/geek/jobs',
        'login': 'https://www.zhipin.com/web/user/?intent=0',
        'search': 'https://www.zhipin.com/web/geek/jobs?query={keyword}&city={city_code}&page={page}',
        'hosts': ['www.zhipin.com', 'zhipin.com'],
        'cards': ['.job-card-wrapper', '.job-card-box', '.job-list-box .job-card'],
        'link': ['a[href*="/job_detail/"]'],
        'title': ['.job-name', '.job-title'], 'company': ['.company-name', '.boss-name .company-name','.job-card-footer .boss-name'],
        'salary': ['.job-salary','.salary'], 'location': ['.company-location','.job-area', '.job-area-wrapper', '.area'],
        'description': ['.job-sec-text', '.job-detail-body', '.job-detail-section'],
        'account': ['.nav-figure .label-name', '.nav-figure .name', '.user-nav .user-name', '.nav-figure img[title]',
                    'a[href="https://www.zhipin.com/web/geek/recommend"]'],
        'contact': ['a.op-btn-chat', '.btn-startchat', 'a:has-text("立即沟通")', 'button:has-text("立即沟通")'],
        'continue_chat': ['button:has-text("继续沟通")', 'a:has-text("继续沟通")'],
        'chat_root': ['.chat-conversation', '.chat-message-list', '.chat-message'],
        'messages_out': ['.message-item.item-myself', '.message-item.is-self', '.chat-message .message-right', '.message-item.sent'],
        'input': ['#chat-input', 'textarea.chat-input', '[contenteditable="true"].chat-input'],
        'send': ['button.btn-send', '.btn-send', 'button:has-text("发送")'],
        'resume_button': ['.toolbar-btn:has-text("发简历")', 'button:has-text("发简历")', 'a:has-text("发简历")', '.btn-resume'],
        'resume_dialog': ['.dialog-wrap:visible', '.dialog-container:visible', '[role="dialog"]:visible'],
        'resume_confirm': ['button:has-text("发送")', 'button:has-text("确定")', 'button:has-text("发送简历")'],
        'blocked': ['职位已关闭', '该职位已下线', '该职位已停止招聘'],
    },
    'zhilian': {
        'label': '智联招聘', 'home': 'https://www.zhaopin.com/',
        'search': 'https://www.zhaopin.com/sou/jl{city_code}/?kw={keyword}&p={page}',
        'hosts': ['www.zhaopin.com', 'zhaopin.com', 'sou.zhaopin.com', 'jobs.zhaopin.com', 'i.zhaopin.com'],
        'cards': ['.joblist-box__item', '.positionlist__list-item', '.joblist-box .joblist-item'],
        'link': ['a[href*="jobs.zhaopin.com/"]', 'a[href*="/jobdetail/"]'],
        'title': ['.jobinfo__name', '.job-name', '.positionlist__jobname'],
        'company': ['.companyinfo__name', '.company-name'],
        'salary': ['.jobinfo__salary', '.salary'],
        'location': ['.jobinfo__other-info-item', '.job-address', '.city'],
        'description': ['.describtion__detail-content', '.job-description', '.job-detail'],
        'account': ['.user-name', '.user-info__name', '.login-info__name', '.user__name'],
        'apply': ['button:has-text("申请职位")', 'a:has-text("申请职位")', 'button:has-text("立即投递")', '.a-job-apply'],
        'applied': ['button:has-text("已申请")', 'a:has-text("已申请")', 'button:has-text("已投递")', '.apply-success'],
        'resume_dialog': ['[role="dialog"]:visible', '.resume-select:visible', '.el-dialog:visible'],
        'resume_confirm': ['button:has-text("确认投递")', 'button:has-text("立即投递")', 'button:has-text("确定")'],
        'blocked': ['职位已下线', '职位已停止招聘', '职位已关闭'],
    },
}
