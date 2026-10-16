from core.control import NeedHuman
from platforms.base import ApplicantPlatform, BeforeClickError

class ZhilianApplicant(ApplicantPlatform):
    def __init__(self, *args, **kwargs):
        super().__init__('zhilian', *args, **kwargs)

    def apply(self, job, resume, scope):
        self.verify_job(job)
        self.ensure_session(scope)
        if self.first(self.spec['applied']):
            return {'job':job['external_id'], 'evidence':'岗位页面显示已申请/已投递', 'existing':True}
        button = self.unique(self.spec['apply'])
        if not button:
            raise BeforeClickError('未识别到申请职位入口')
        # An immediate-apply button can use the online resume without a picker.
        # The caller explicitly chooses platform-resume mode in the plan.
        self.external_click(button)
        self.page.wait_for_timeout(500)
        dialog = self.unique(self.spec['resume_dialog'])
        if dialog:
            if resume:
                choice = dialog.get_by_text(resume['name'], exact=True)
                if choice.count() != 1:
                    raise NeedHuman('智联简历选择框未找到指定简历，请人工处理')
                choice.click()
            self.verify_job(job)
            self.ensure_session(scope)
            confirm = self.unique(self.spec['resume_confirm'], dialog)
            if not confirm:
                raise NeedHuman('未识别到智联投递确认按钮')
            confirm.click()
        for _ in range(25):
            self.checkpoint()
            self.verify_job(job)
            if self.first(self.spec['applied']):
                return {'job':job['external_id'], 'evidence':'岗位页面显示已申请/已投递'}
            self.page.wait_for_timeout(300)
        raise NeedHuman('已点击申请但结果未确认，请人工核验，不会自动重投')
