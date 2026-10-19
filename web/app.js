const { createApp, ref, computed, onMounted, onUnmounted } = Vue;
const icons = {
  dashboard:'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
  file:'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z M14 2v6h6 M8 13h8 M8 17h5',
  sliders:'M4 21v-7 M4 10V3 M12 21v-9 M12 8V3 M20 21v-5 M20 12V3 M1 14h6 M9 8h6 M17 16h6',
  briefcase:'M3 7h18v14H3z M8 7V3h8v4 M3 12a25 25 0 0 0 18 0 M12 11v4',
  settings:'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1z',
  plus:'M12 5v14 M5 12h14', refresh:'M20 7v5h-5 M4 17v-5h5 M6 6a8 8 0 0 1 13 4 M18 18a8 8 0 0 1-13-4',
  search:'M21 21l-5-5 M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14', play:'M8 4l13 8-13 8z',
  shield:'M12 3l8 3v6c0 5-8 9-8 9S4 17 4 12V6z M8 12l3 3 5-6',
  activity:'M3 12h4l3-8 4 16 3-8h4', download:'M12 3v12 M7 10l5 5 5-5 M4 16v5h16v-5',
  check:'M4 12l5 5L20 6', clock:'M12 8v5l3 2 M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18'
};
const app = createApp({
  setup() {
    const nav = [
      {id:'dashboard',label:'工作台',icon:'dashboard',eyebrow:'YOUR NEXT CHAPTER',description:'把精力留给合适的机会，让每一步求职更有条理。'},
      {id:'resumes',label:'我的简历',icon:'file',eyebrow:'YOUR EXPERIENCE',description:'管理真实经历与简历版本，为不同方向准备合适的表达。'},
      {id:'plans',label:'求职方案',icon:'sliders',eyebrow:'A CLEAR DIRECTION',description:'设置你的求职偏好，让筛选与投递围绕目标展开。'},
      {id:'applications',label:'岗位与投递',icon:'briefcase',eyebrow:'EVERY OPPORTUNITY',description:'查看岗位匹配情况，追踪投递结果与待处理事项。'},
      {id:'settings',label:'设置',icon:'settings',eyebrow:'YOUR WORKSPACE',description:'了解本地数据、平台会话和任务恢复方式。'}
    ];
    const initialPage=new URLSearchParams(location.search).get('page');
    const page=ref(nav.some(n=>n.id===initialPage)?initialPage:'dashboard'), recordTab=ref('jobs'), state=ref({platforms:{},state:'idle'}), connected=ref(true);
    const overview=ref({events:[]}), resumes=ref([]), plans=ref([]), jobs=ref([]), applications=ref([]), settings=ref({});
    const selectedPlan=ref(localStorage.getItem('selectedPlan')||''), selectedPlatforms=ref(['boss']), selectedJobs=ref([]);
    const jobQuery=ref(''), jobPlatform=ref(''), onlyEligible=ref(false), planForm=ref(null), preview=ref(null), jobDetail=ref(null),applicationDetail=ref(null);
    const messagePreview=ref(null);
    const workspacePath=ref(''),resumeMaxContacts=ref(5);
    const resumeCity=ref('厦门'),resumeCityCode=ref('101230200'),resumeUseAI=ref(true),aiForm=ref(null),aiTesting=ref(false);
    const toast=ref(null), uploading=ref(false), saving=ref(false), reviewNotes=ref({}), completionNote=ref(''), confirmRun=ref(false);
    const platformNames={boss:'BOSS直聘',zhilian:'智联招聘'};
    const metrics=[{key:'jobs',label:'已发现岗位',icon:'search',note:'累计读取的岗位机会'},{key:'eligible',label:'符合条件',icon:'check',note:'通过最近一次方案筛选'},{key:'delivered',label:'已核验投递',icon:'briefcase',note:'完成所需动作的申请'},{key:'review',label:'需要核验',icon:'clock',note:'打开平台确认操作结果'}];
    let timer, toastTimer, polling=false;
    function notify(text,kind='info'){ clearTimeout(toastTimer);toast.value={text,kind};toastTimer=setTimeout(()=>toast.value=null,6000); }
    async function api(path,opts={}){
      const headers={'X-App-Token':document.querySelector('meta[name=app-token]').content,...opts.headers};
      if(opts.body && !(opts.body instanceof FormData)) headers['Content-Type']='application/json';
      const response=await fetch('/api'+path,{...opts,headers});
      if(!response.ok){const data=await response.json().catch(()=>({detail:'本地服务请求失败'}));let message=data.detail;if(Array.isArray(message))message=message.map(x=>`${x.loc.slice(1).join('.')}：${x.msg}`).join('；');throw new Error(message||'操作失败');}
      return response.json();
    }
    async function refresh(){
      const results=await Promise.allSettled([api('/state'),api('/overview'),api('/plans'),api('/resumes'),api('/jobs'),api('/applications'),api('/settings')]);
      const refs=[state,overview,plans,resumes,jobs,applications,settings];
      results.forEach((r,i)=>{if(r.status==='fulfilled')refs[i].value=r.value;});
      if(!aiForm.value&&settings.value.ai){const {has_api_key,...value}=settings.value.ai;aiForm.value={...value,api_key:''};}
      connected.value=results.every(r=>r.status==='fulfilled');
      if(!plans.value.some(p=>p.id===selectedPlan.value))selectedPlan.value=plans.value[0]?.id||'';
      selectedJobs.value=selectedJobs.value.filter(id=>jobs.value.some(j=>j.id===id));
      if(applicationDetail.value)applicationDetail.value=applications.value.find(a=>a.id===applicationDetail.value.id)||null;
    }
    async function poll(){if(polling)return;polling=true;try{const before=state.value.state+'|'+state.value.run_id;const results=await Promise.all([api('/state'),api('/overview')]);state.value=results[0];overview.value=results[1];connected.value=true;const after=state.value.state+'|'+state.value.run_id;if(before!==after && !state.value.busy)await refresh();}catch{connected.value=false;}finally{polling=false;}}
    async function act(fn){try{await fn();await refresh();}catch(e){notify(e.message,'error');}}
    const currentNav=computed(()=>nav.find(n=>n.id===page.value)),activePlan=computed(()=>plans.value.find(p=>p.id===selectedPlan.value));
    const filteredRecords=computed(()=>(recordTab.value==='jobs'?jobs.value:applications.value).filter(r=>(!jobPlatform.value||r.platform===jobPlatform.value)&&(!jobQuery.value||(r.title+' '+r.company).toLowerCase().includes(jobQuery.value.toLowerCase()))&&(recordTab.value!=='jobs'||!onlyEligible.value||r.eligible)));
    const recoverableRun=computed(()=>{const r=overview.value.runs?.[0];return r&&['interrupted','needs_human','error','stopped'].includes(r.status)?r:null;});
    function retryRun(){return act(()=>api('/runs/'+recoverableRun.value.id+'/retry',{method:'POST'}));}
    const today=new Intl.DateTimeFormat('zh-CN',{year:'numeric',month:'long',day:'numeric',weekday:'short'}).format(new Date());
    function statusName(value){return ({idle:'待开始',queued:'已排队',running:'运行中',paused:'已暂停',completed:'已完成',stopped:'已停止',needs_human:'需要人工处理',error:'运行异常',disconnected:'未连接',closed:'已关闭',waiting_login:'等待平台登录',ready:'已就绪',delivered:'投递已核验',needs_review:'结果待核验',waiting_reply:'等待 HR 回复后发简历',message_sent:'沟通文案已发送',previously_contacted:'已沟通过，已跳过',unavailable:'暂时无法定位，已跳过',sent:'已确认成功',unknown:'结果待核验',pending:'执行中',failed:'确认未发生',interrupted:'中断待恢复'})[value]||value||'未连接';}
    function formatDate(value){if(!value)return '';return new Intl.DateTimeFormat('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value));}
    function resumeName(id){return resumes.value.find(r=>r.id===id)?.name||'未选择简历';}
    function splitTerms(text){return (text||'').split(/[,，;；\n]/).map(s=>s.trim()).filter(Boolean);}
    function recordUrl(row){return row.body?.url||row.url;}
    function editPlan(plan){planForm.value=plan?{max_greetings:plan.max_deliveries||5,matching_mode:'rules',greeting_style:'resume_evidence',communication_only:false,skip_existing_contacts:true,personalized_greeting:true,...JSON.parse(JSON.stringify(plan))}:{matching_mode:'rules',id:'',name:'',keyword:'',city:'',boss_city_code:'100010000',zhilian_city_code:'489',salary_min:0,skills:'',exclude_keywords:'',min_score:60,max_greetings:5,max_pages:1,max_jobs:10,max_deliveries:5,interval_seconds:60,resume_id:resumes.value[0]?.id||'',greeting:'',greeting_style:'job_interest',communication_only:true,skip_existing_contacts:true,personalized_greeting:true,zhilian_online_resume:false};}
    async function savePlan(){saving.value=true;try{const plan=await api('/plans',{method:'POST',body:JSON.stringify(planForm.value)});selectedPlan.value=plan.id;localStorage.setItem('selectedPlan',plan.id);planForm.value=null;notify('求职方案已保存');await refresh();}catch(e){notify(e.message,'error');}finally{saving.value=false;}}
    function deletePlan(p){if(window.confirm(`删除方案“${p.name}”？已保存的投递记录会保留。`))act(()=>api('/plans/'+p.id,{method:'DELETE'}));}
    async function uploadResume(event){if(uploading.value)return;const file=event.target.files[0];if(!file)return;if(file.size>15*1024*1024){notify('简历不能超过15MB','error');event.target.value='';return;}uploading.value=true;const form=new FormData();form.append('file',file);try{await act(async()=>{const result=await api('/resumes',{method:'POST',body:form});preview.value=result;notify('简历已上传并自动读取，请核对解析结果');});}finally{uploading.value=false;event.target.value='';}}
    async function prepareResumeTask(r){await act(async()=>{const strategy=await api('/resumes/'+r.id+'/strategy');preview.value={...r,strategy};});}
    async function startResumeTask(r){await act(async()=>{const result=await api('/resumes/'+r.id+'/auto-run',{method:'POST',body:JSON.stringify({city:resumeCity.value,boss_city_code:resumeCityCode.value,max_contacts:resumeMaxContacts.value,matching_mode:resumeUseAI.value?'ai':'rules'})});selectedPlan.value=result.plan_id;localStorage.setItem('selectedPlan',result.plan_id);preview.value=null;page.value='dashboard';notify('已按简历启动：小批搜索、匹配后打招呼；异常时整轮停止');});}
    async function pickWorkspace(){await act(async()=>{const r=await api('/settings/workspace/pick',{method:'POST'});if(r.data_dir)workspacePath.value=r.data_dir;});}
    async function saveWorkspace(){await act(async()=>{await api('/settings/workspace',{method:'PUT',body:JSON.stringify({data_dir:workspacePath.value})});notify('目录已保存，退出并重新启动后生效；原资料保留在原目录');});}
    async function saveAI(){await act(async()=>{await api('/settings/ai',{method:'PUT',body:JSON.stringify(aiForm.value)});aiForm.value=null;notify('大模型配置已保存，密钥不回传');});}
    async function testAI(){aiTesting.value=true;try{await act(async()=>{const result=await api('/settings/ai/test',{method:'POST'});notify('模型测试通过：'+result.model+'（仅使用合成样例）');});}finally{aiTesting.value=false;}}
    function deleteResume(r){if(window.confirm(`删除简历“${r.name}”？`))act(()=>api('/resumes/'+r.id,{method:'DELETE'}));}
    function platform(code,operation){return act(()=>api(`/platforms/${code}/${operation}`,{method:'POST'}));}
    function control(operation){return act(()=>api('/control/'+operation,{method:'POST'}));}
    async function start(mode,ids=[]){
      if(!selectedPlan.value){notify('请先创建并选择求职方案','error');return;}
      localStorage.setItem('selectedPlan',selectedPlan.value);
      let platforms=selectedPlatforms.value;
      if(ids.length)platforms=[...new Set(jobs.value.filter(j=>ids.includes(j.id)).map(j=>j.platform))];
      await act(async()=>{await api('/runs',{method:'POST',body:JSON.stringify({plan_id:selectedPlan.value,mode,platforms,job_ids:ids})});page.value='dashboard';notify(mode==='search'?'岗位搜索任务已启动':'自动投递任务已启动');});
    }
    async function completeApplication(){const note=completionNote.value.trim();if(note.length<5){notify('请填写人工核验依据','error');return;}await act(async()=>{await api('/applications/'+applicationDetail.value.id+'/complete',{method:'POST',body:JSON.stringify({note})});completionNote.value='';notify('已记录人工完成结果');});}
    async function previewMessage(){messagePreview.value=null;await act(async()=>{messagePreview.value=await api('/jobs/'+encodeURIComponent(jobDetail.value.id)+'/message-preview?plan_id='+encodeURIComponent(selectedPlan.value));});}
    function actionName(kind){return ({upload:'准备平台附件',contact:'建立沟通会话',greeting:'发送沟通文案',resume:'发送附件简历',apply:'提交职位申请',manual_confirmation:'人工完成核验'})[kind]||kind;}
    async function reviewAction(action,sent){const note=reviewNotes.value[action.id]||'';if(note.trim().length<5){notify('请填写不少于5字的核验依据','error');return;}await act(async()=>{await api('/actions/'+action.id+'/review',{method:'POST',body:JSON.stringify({sent,note})});notify('核验结果已保存');});}
    onMounted(async()=>{await refresh();timer=setInterval(poll,2000);});onUnmounted(()=>{clearInterval(timer);clearTimeout(toastTimer);});
    return {pickWorkspace,workspacePath,saveWorkspace,resumeMaxContacts,aiForm,aiTesting,saveAI,testAI,resumeUseAI,resumeCity,resumeCityCode,prepareResumeTask,startResumeTask,messagePreview,previewMessage,nav,page,recordTab,state,connected,overview,resumes,plans,jobs,applications,settings,selectedPlan,selectedPlatforms,selectedJobs,jobQuery,jobPlatform,onlyEligible,planForm,preview,jobDetail,applicationDetail,toast,saving,reviewNotes,platformNames,metrics,currentNav,activePlan,filteredRecords,today,confirmRun,refresh,statusName,formatDate,resumeName,splitTerms,recordUrl,editPlan,savePlan,deletePlan,uploadResume,deleteResume,platform,control,start,actionName,reviewAction,completionNote,completeApplication,recoverableRun,retryRun,uploading};
  }
});
app.component('icon',{props:['name'],template:'<svg class="icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path :d="path" /></svg>',computed:{path(){return icons[this.name]||icons.file;}}});
app.mount('#app');
