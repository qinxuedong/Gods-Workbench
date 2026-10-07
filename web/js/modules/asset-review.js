/**
 * Copyright 2026 Gods-Workbench Authors
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

(function(){
    'use strict';

    const state = {
        auth:null,
        asset:null,
        context:{asset_id:'',asset_version_id:'',project_id:'',team_id:'',canvas_id:''},
        sessions:[],
        detail:null,
        activeSessionId:'',
        draftAnnotation:null,
        drawMode:'pin',
        drawStart:null,
        drawPoints:[],
        timecodeMs:null,
        ws:null,
        users:[],
        teams:[],
        approvals:[],
        teamTab:'members',
        approvalStatus:'pending',
        teamLoading:false,
        wsRetryTimer:null,
        wsRetryCount:0,
        wsBlocked:false,
        parentModalHost:false,
    };

    const q = (selector, root=document) => root.querySelector(selector);
    const qa = (selector, root=document) => [...root.querySelectorAll(selector)];
    const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    const attr = esc;
    const REVIEW_CONTEXT_KEYS = ['asset_id','asset_version_id','project_id','team_id','canvas_id'];
    const REVIEW_REQUIRED_KEYS = ['asset_id','asset_version_id','project_id','team_id'];
    let reviewReady = false;
    let pendingReviewContext = null;
    let reviewOpenKey = '';
    let reviewOpenPromise = null;
    const roleLevel = role => ({reviewer:20,editor:30,admin:40}[role] || 0);
    // 授权判定必须失败关闭：只有后端确认 authenticated === true 且 principal 存在时才比对角色等级。
    // 历史缺陷（独立复核 R6-9）：旧实现为 `!state.auth?.auth_required || ...`，而后端
    // GET /api/asset-auth/status 的契约里根本没有 auth_required 字段（见 api/routes_auth.py
    // 的 auth_status()），`!undefined === true` 使该表达式恒为 true，导致**未认证访客**
    // 被判定拥有 admin/editor/reviewer 权限。
    const can = role => Boolean(state.auth?.authenticated && state.auth?.principal)
        && roleLevel(state.auth?.principal?.role) >= roleLevel(role);
    // 「是否需要先登录」的唯一口径：以服务端确认的 authenticated 为准，
    // 不再依赖后端不存在的 auth_required 字段。
    const needsLogin = () => !(state.auth?.authenticated && state.auth?.principal);
    const icon = (name, size=16) => '<i data-lucide="' + name + '" style="width:'+size+'px;height:'+size+'px"></i>';

    function reviewContextValues(context={}){
        return REVIEW_CONTEXT_KEYS.reduce((result, key) => {
            const value = String(context?.[key] || '').trim();
            if(value) result[key] = value;
            return result;
        }, {});
    }

    function reviewContextKey(context={}){
        const values = reviewContextValues(context);
        return REVIEW_CONTEXT_KEYS.map(key => values[key] || '').join('|');
    }

    function reviewContextFromUrl(search=location.search){
        const params = new URLSearchParams(search || '');
        const context = reviewContextValues(Object.fromEntries(REVIEW_CONTEXT_KEYS.map(key => [key, params.get(key) || ''])));
        const marked = params.get('asset_review') === '1';
        if(!marked && !REVIEW_REQUIRED_KEYS.every(key => context[key])) return null;
        if(!REVIEW_REQUIRED_KEYS.every(key => context[key])) return null;
        return context;
    }

    function syncReviewUrl(context){
        const values = reviewContextValues(context);
        if(!REVIEW_REQUIRED_KEYS.every(key => values[key])) return;
        try {
            const url = new URL(location.href);
            ['asset_review', ...REVIEW_CONTEXT_KEYS].forEach(key => url.searchParams.delete(key));
            url.searchParams.set('asset_review', '1');
            Object.entries(values).forEach(([key, value]) => url.searchParams.set(key, value));
            history.replaceState({}, '', `${url.pathname}${url.search}${url.hash}`);
        } catch(_) {}
        try {
            if(parent !== window && typeof parent.syncAssetReviewDeepLink === 'function') parent.syncAssetReviewDeepLink(values);
        } catch(_) {}
    }

    function clearReviewUrl(){
        try {
            const url = new URL(location.href);
            ['asset_review', ...REVIEW_CONTEXT_KEYS].forEach(key => url.searchParams.delete(key));
            history.replaceState({}, '', `${url.pathname}${url.search}${url.hash}`);
        } catch(_) {}
        try {
            if(parent !== window && typeof parent.clearAssetReviewDeepLink === 'function') parent.clearAssetReviewDeepLink({notify:false});
        } catch(_) {}
    }

    function assetReviewApi(){
        const api = globalThis.GodsWorkbenchAssetReviewApi;
        if(!api) throw new Error('审阅服务尚未加载');
        return api;
    }

    function assetAuthApi(){
        const api = globalThis.GodsWorkbenchAssetAuthApi;
        if(!api) throw new Error('账号服务尚未加载');
        return api;
    }

    async function reviewJsonResponse(response){
        const res = await response;
        const data = await res.json().catch(() => ({}));
        if(res.status === 401) openAuth();
        if(!res.ok) throw new Error(data.detail || data.message || '操作失败');
        return data;
    }

    function toast(message, error=false){
        let node = q('#assetReviewToast');
        if(!node){
            node = document.createElement('div');
            node.id = 'assetReviewToast';
            node.className = 'asset-review-toast';
            document.body.appendChild(node);
        }
        node.textContent = message;
        node.classList.toggle('error', error);
        node.classList.add('show');
        clearTimeout(node._timer);
        node._timer = setTimeout(() => node.classList.remove('show'), 2600);
    }

    function ensureLayer(){
        let layer = q('#assetReviewLayer');
        if(!layer){
            layer = document.createElement('div');
            layer.id = 'assetReviewLayer';
            document.body.appendChild(layer);
        }
        window.FloatingDismissal?.mark(layer, {
            surface: 'asset-review-layer',
            kind: 'modal',
            content: '.asset-account-modal, .asset-team-modal, .asset-review-drawer, .asset-share-modal',
            close: closeReviewLayer,
            isOpen: element => /(?:^|\s)(?:auth-open|team-open|review-open|share-open)(?:\s|$)/.test(String(element.className || '')),
        });
        return layer;
    }

    function notifyParentModalHost(active, force=false){
        if(!force && !state.parentModalHost) return;
        if(active) document.body.classList.add('asset-modal-host');
        try {
            if(parent !== window) parent.postMessage({type:active ? 'asset-modal-host-open' : 'asset-modal-host-close'}, location.origin);
        } catch(_) {}
    }

    function notifyParentAuthChanged(){
        try {
            if(parent !== window) parent.postMessage({type:'asset-auth-changed'}, location.origin);
        } catch(_) {}
    }

    function closeLayer(options={}){
        const releaseHost = options.releaseHost !== false;
        if(releaseHost && state.parentModalHost) notifyParentModalHost(false, true);
        const layer = ensureLayer();
        layer.innerHTML = '';
        layer.className = '';
        state.asset = null;
        state.detail = null;
        state.draftAnnotation = null;
        if(releaseHost) state.parentModalHost = false;
    }

    function closeReviewLayer(){
        const layer = ensureLayer();
        const isReviewRoute = layer.classList.contains('review-open') || layer.classList.contains('share-open');
        closeLayer();
        if(isReviewRoute) clearReviewUrl();
        return true;
    }

    async function loadAuth(){
        try {
            state.auth = await reviewJsonResponse(assetAuthApi().getStatus());
            state.authDegraded = false;
        } catch(error){
            // 认证状态端点不可达：**显式降级**，绝不写入放宽权限的默认值。
            // 历史缺陷（R6-9）：旧 catch 写入 {auth_required:false}，配合旧 can() 等于无条件放行。
            state.auth = {
                degraded:true,
                reason:'认证服务未接入或不可达',
                auth_mode:'unknown',
                oidc_ready:false,
                authenticated:false,
                principal:null,
                login_available:false,
                logout_available:false,
            };
            state.authDegraded = true;
            toast('认证服务未接入或不可达：审阅与团队特权操作已禁用', true);
        }
        renderAccountButton();
        notifyParentAuthChanged();
        if(needsLogin() && state.auth?.login_available) openAuth();
    }

    function renderAccountButton(){
        q('#assetAccountBtn')?.remove();
        q('#assetTeamBtn')?.remove();
    }

    function openAuth(options={}){
        if(typeof options.host === 'boolean') state.parentModalHost = options.host;
        const layer = ensureLayer();
        const loginAvailable = Boolean(state.auth?.login_available);
        const user = state.auth?.principal;
        layer.className = 'asset-review-layer auth-open';
        if(user){
            layer.innerHTML = '<div class="asset-review-backdrop" data-review-close></div><section class="asset-account-modal" data-floating-content>' +
                '<header><div><strong>账号与 PC Token</strong><span>' + esc(user.username) + ' · ' + esc(user.role) + '</span></div><button data-review-close>'+icon('x')+'</button></header>' +
                '<div class="asset-account-body"><div class="account-profile">'+icon('shield-check',24)+'<div><strong>'+esc(user.display_name)+'</strong><span>当前角色：'+esc(user.role)+'</span></div></div>' +
                (user.local_mode ? '<p class="review-hint">当前为免登录本地模式。NAS 部署时设置 ASSET_AUTH_REQUIRED=1 后可创建用户与 PC Token。</p>' :
                '<label>Token 名称<input id="reviewTokenName" value="PC 客户端"></label><button class="review-primary" data-review-token-create>'+icon('key-round')+'生成 Token</button><div id="reviewTokenResult"></div>') +
                (user.role==='admin'?'<section id="reviewAdminPanel" class="review-admin-panel"><div class="share-spinner"></div></section>':'')+
                '</div><footer><button data-review-logout>退出登录</button><button class="review-primary" data-review-close>完成</button></footer></section>';
        } else {
            // 本切片认证由**外部 IdP**（OIDC 授权码 + PKCE）承担：本页**不收集**任何用户名或口令。
            // 历史缺陷（R6-9）：旧实现渲染用户名/口令表单并 POST 给 /api/asset-auth/login，
            // 而新后端该端点不消费任何请求体，只返回 {authorization_url, state} —— 属静默坏掉。
            layer.innerHTML = '<div class="asset-review-backdrop"></div><section class="asset-account-modal" data-floating-content>' +
                '<header><div><strong>登录资产中心</strong><span>通过外部身份提供商（OIDC 授权码 + PKCE）继续</span></div></header>' +
                '<form id="assetAuthForm" class="asset-account-body">' +
                '<p class="review-hint"'+(loginAvailable?'':' data-gw-degradation="not_integrated"')+'>本页不收集用户名或密码；凭据只在 IdP 页面输入。'+(loginAvailable?'':'认证服务未接入或未配置：'+esc(state.auth?.reason || '未启用外部 IdP 校验'))+'</p>' +
                '<button class="review-primary" type="submit" '+(loginAvailable?'':'disabled')+'>'+icon('log-in')+'使用外部身份提供商登录</button>' +
                '<p class="review-auth-error" id="assetAuthError"></p></form></section>';
        }
        window.lucide?.createIcons();
        if(state.parentModalHost) notifyParentModalHost(true);
        if(user?.role==='admin') loadAdminPanel().catch(error=>toast(error.message,true));
    }

    async function loadAdminPanel(){
        const panelBeforeLoad=q('#reviewAdminPanel');
        const openDetails=panelBeforeLoad
            ? qa('details',panelBeforeLoad).map(detail=>Boolean(detail.open))
            : [];
        const [userData,teamData]=await Promise.all([
            reviewJsonResponse(assetAuthApi().listUsers()),
            reviewJsonResponse(assetAuthApi().listTeams()),
        ]);
        state.users=userData.users||[];state.teams=teamData.teams||[];
        const panel=q('#reviewAdminPanel');if(!panel)return;
        panel.innerHTML='<details><summary>用户与角色 <span>'+state.users.length+'</span></summary><div class="review-admin-content">'+
            '<form id="assetUserCreateForm"><input name="username" placeholder="用户名" required><input name="display_name" placeholder="显示名称" required><input name="password" type="password" placeholder="初始密码（至少8位）" minlength="8" required><select name="role"><option value="reviewer">reviewer</option><option value="editor">editor</option><option value="admin">admin</option></select><button class="review-primary" type="submit">创建用户</button></form>'+
            '<div class="review-admin-list">'+state.users.map(user=>'<div><span><strong>'+esc(user.display_name)+'</strong><small>'+esc(user.username)+'</small></span><em>'+esc(user.role)+'</em></div>').join('')+'</div></div></details>'+
            '<details><summary>团队 <span>'+state.teams.length+'</span></summary><div class="review-admin-content"><form id="assetTeamCreateForm"><input name="name" placeholder="团队名称" required><button class="review-primary" type="submit">创建团队</button></form>'+
            (state.teams.length?'<form id="assetTeamMemberForm"><select name="team_id">'+state.teams.map(team=>'<option value="'+attr(team.id)+'">'+esc(team.name)+'</option>').join('')+'</select><select name="user_id">'+state.users.map(user=>'<option value="'+attr(user.id)+'">'+esc(user.display_name)+' · '+esc(roleName(user.role))+'</option>').join('')+'</select><button type="submit">加入/更新</button></form>':'')+
            '<div class="review-admin-list">'+state.teams.map(team=>'<div><span><strong>'+esc(team.name)+'</strong><small>'+(team.members||[]).map(member=>esc(member.display_name)+' · '+esc(member.role)).join('，')+'</small></span><em>'+Number(team.member_count||0)+'</em></div>').join('')+'</div></div></details>';
        qa('details',panel).forEach((detail,index)=>{ if(openDetails[index]) detail.open=true; });
    }

    async function createAdminUser(form){
        const payload=Object.fromEntries(new FormData(form).entries());
        await reviewJsonResponse(assetAuthApi().createUser(payload));
        form.reset();await loadAdminPanel();toast('用户已创建');
    }

    async function createAdminTeam(form){
        if(form.dataset.submitting==='1')return;
        const button=form.querySelector('button[type="submit"]');
        const originalLabel=button?.textContent||'创建团队';
        form.dataset.submitting='1';
        if(button){button.disabled=true;button.textContent='创建中…';}
        const payload=Object.fromEntries(new FormData(form).entries());
        try{
            await reviewJsonResponse(assetAuthApi().createTeam(payload));
            form.reset();await loadAdminPanel();toast('团队已创建');
        }finally{
            form.dataset.submitting='';
            if(button){button.disabled=false;button.textContent=originalLabel;}
        }
    }

    async function setAdminTeamMember(form){
        const payload=Object.fromEntries(new FormData(form).entries());
        await reviewJsonResponse(assetAuthApi().updateTeamMember(payload.team_id, {
            user_id:payload.user_id,
        }));
        await loadAdminPanel();toast('团队成员已更新');
    }

    const roleName = role => ({reviewer:'审核者',editor:'编辑者',admin:'管理员'}[role] || role || '未知');
    const statusName = status => ({active:'已启用',disabled:'已停用',pending:'待审批',approved:'已通过',rejected:'已拒绝'}[status] || status || '未知');
    const approvalActionName = action => ({
        'text.save':'保存文本正文', 'text.version_edit':'编辑文本版本', 'text.version_delete':'删除文本版本',
        'text.version_restore':'恢复文本版本', 'user.admin_create':'创建成员账户', 'user.update':'修改成员账户',
        'user.delete':'删除成员账户',
        'team.create':'创建团队', 'team.delete':'删除团队', 'team.member_update':'修改团队成员', 'team.member_remove':'移除团队成员',
    }[action] || action || '系统操作');
    const roleOptions = selected => ['reviewer','editor','admin'].map(role =>
        '<option value="'+role+'" '+(role===selected?'selected':'')+'>'+roleName(role)+'</option>'
    ).join('');

    function teamDate(value){
        const timestamp = Number(value || 0);
        return timestamp ? new Date(timestamp).toLocaleString('zh-CN', {hour12:false}) : '未知时间';
    }

    function renderTeamMembersPanel(){
        const principalId = state.auth?.principal?.id || '';
        return '<section class="team-management-panel">' +
            '<div class="team-section-head"><div><strong>成员账户</strong><span>创建账户并分配全局权限</span></div><em>'+state.users.length+' 人</em></div>' +
            '<form id="teamUserCreateForm" class="team-create-grid">' +
                '<label><span>用户名</span><input name="username" required maxlength="64" placeholder="登录用户名"></label>' +
                '<label><span>显示名称</span><input name="display_name" required maxlength="120" placeholder="操作记录显示名称"></label>' +
                '<label><span>初始密码</span><input name="password" type="password" minlength="8" required placeholder="至少 8 位"></label>' +
                '<label><span>账户权限</span><select name="role">'+roleOptions('reviewer')+'</select></label>' +
                '<button class="review-primary" type="submit">'+icon('user-plus')+'创建账户</button>' +
            '</form>' +
            '<div class="team-user-table"><div class="team-user-table-head"><span>成员</span><span>权限</span><span>状态</span><span>重设密码</span><span>操作</span></div>' +
            (state.users.length ? state.users.map(user => {
                const isSelf = user.id === principalId;
                return '<div class="team-user-row" data-team-user-row="'+attr(user.id)+'">' +
                    '<span class="team-user-identity"><input data-team-user-display value="'+attr(user.display_name || '')+'" aria-label="显示名称"><small>@'+esc(user.username || '')+(isSelf?' · 当前账户':'')+'</small></span>' +
                    '<select data-team-user-role '+(isSelf?'disabled title="不能修改当前账户权限"':'')+'>'+roleOptions(user.role)+'</select>' +
                    '<select data-team-user-status '+(isSelf?'disabled title="不能停用当前账户"':'')+'><option value="active" '+(user.status==='active'?'selected':'')+'>已启用</option><option value="disabled" '+(user.status==='disabled'?'selected':'')+'>已停用</option></select>' +
                    '<input data-team-user-password type="password" minlength="8" placeholder="留空不修改" aria-label="新密码">' +
                    '<span class="team-user-actions"><button type="button" data-team-user-save="'+attr(user.id)+'">'+icon('save')+'保存</button>' +
                    (isSelf ? '' : '<button class="danger" type="button" data-team-user-delete="'+attr(user.id)+'" data-user-name="'+attr(user.display_name || user.username || '')+'">'+icon('trash-2')+'删除</button>') + '</span>' +
                '</div>';
            }).join('') : '<div class="team-empty">暂无协作账户，可从上方创建。</div>') + '</div></section>';
    }

    function renderTeamsPanel(){
        return '<section class="team-management-panel">' +
            '<div class="team-section-head"><div><strong>团队与成员</strong><span>配置团队成员及团队内权限</span></div><em>'+state.teams.length+' 个团队</em></div>' +
            '<form id="teamCreateForm" class="team-create-inline"><input name="name" maxlength="120" required placeholder="新团队名称"><button class="review-primary" type="submit">'+icon('plus')+'创建团队</button></form>' +
            '<div class="team-group-list">' + (state.teams.length ? state.teams.map(team => {
                const members = team.members || [];
                return '<article class="team-group"><header><div><strong>'+esc(team.name)+'</strong><span>'+members.length+' 位成员</span></div><div class="team-group-actions"><code>'+esc(team.slug || '')+'</code><button class="danger" type="button" data-team-delete="'+attr(team.id)+'" data-team-name="'+attr(team.name || '')+'">'+icon('trash-2')+'删除团队</button></div></header>' +
                    '<div class="team-member-list">' + (members.length ? members.map(member =>
                        '<div class="team-member-row" data-team-member-row="'+attr(team.id)+'|'+attr(member.user_id)+'"><span><strong>'+esc(member.display_name || member.username)+'</strong><small>@'+esc(member.username || '')+'</small></span>' +
                        '<select data-team-member-role>'+roleOptions(member.role)+'</select><button type="button" data-team-member-save="'+attr(team.id)+'|'+attr(member.user_id)+'">'+icon('save')+'保存</button>' +
                        '<button class="danger" type="button" data-team-member-remove="'+attr(team.id)+'|'+attr(member.user_id)+'">'+icon('user-minus')+'移除</button></div>'
                    ).join('') : '<div class="team-empty compact">该团队暂无成员。</div>') + '</div>' +
                    '<form class="team-member-add" data-team-member-add="'+attr(team.id)+'"><select name="user_id" required><option value="">选择成员账户</option>'+state.users.map(user=>'<option value="'+attr(user.id)+'">'+esc(user.display_name)+' · '+esc(user.username)+' · '+esc(roleName(user.role))+'</option>').join('')+'</select><button type="submit">'+icon('user-plus')+'添加成员</button></form>' +
                '</article>';
            }).join('') : '<div class="team-empty">暂无团队，可从上方创建。</div>') + '</div></section>';
    }

    function renderApprovalsPanel(){
        return '<section class="team-management-panel team-approval-panel">' +
            '<div class="team-section-head"><div><strong>操作审批</strong><span>高权限账户可审批其他低权限账户的操作</span></div><label class="team-filter">筛选<select id="teamApprovalFilter"><option value="pending" '+(state.approvalStatus==='pending'?'selected':'')+'>待审批</option><option value="approved" '+(state.approvalStatus==='approved'?'selected':'')+'>已通过</option><option value="rejected" '+(state.approvalStatus==='rejected'?'selected':'')+'>已拒绝</option><option value="all" '+(state.approvalStatus==='all'?'selected':'')+'>全部</option></select></label></div>' +
            '<div class="team-approval-list">' + (state.approvals.length ? state.approvals.map(approval => {
                const detail = approval.detail || {};
                const pending = approval.status === 'pending';
                const canApprove = pending && approval.can_approve;
                return '<article class="team-approval-row"><header><span class="team-approval-status '+attr(approval.status)+'">'+statusName(approval.status)+'</span><time>'+teamDate(approval.operation_created_at || approval.created_at)+'</time></header>' +
                    '<div class="team-approval-main"><strong>'+esc(approvalActionName(approval.action))+'</strong><p><b>'+esc(approval.requester_name || '未知账户')+'</b> · '+esc(roleName(approval.requester_role))+' · '+esc(detail.file_name || approval.entity_id || '')+'</p></div>' +
                    (pending ? '<div class="team-approval-actions"><input data-team-approval-note="'+attr(approval.id)+'" maxlength="2000" placeholder="审批意见（可选）"><button type="button" data-team-approval-decision="'+attr(approval.id)+'|approved" '+(canApprove?'':'disabled')+'>'+icon('check')+'通过</button><button class="danger" type="button" data-team-approval-decision="'+attr(approval.id)+'|rejected" '+(canApprove?'':'disabled')+'>'+icon('x')+'拒绝</button></div>' : '<footer>审批人：'+esc(approval.reviewer_name || '系统')+(approval.note?' · '+esc(approval.note):'')+'</footer>') +
                    (!pending || canApprove ? '' : '<small class="team-approval-limit">只能审批其他账户且权限低于自己的操作</small>') +
                '</article>';
            }).join('') : '<div class="team-empty">当前筛选下没有审批记录。</div>') + '</div></section>';
    }

    function renderTeamManagement(){
        const layer = ensureLayer();
        if(!layer.classList.contains('team-open')) return;
        const admin = state.auth?.principal?.role === 'admin';
        const tabs = (admin ? [['members','成员账户','users'],['teams','团队','users-round']] : []).concat([['approvals','操作审批','badge-check']]);
        const body = state.teamLoading
            ? '<div class="team-loading"><span class="share-spinner"></span><strong>正在加载团队数据…</strong></div>'
            : state.teamTab === 'members' ? renderTeamMembersPanel() : state.teamTab === 'teams' ? renderTeamsPanel() : renderApprovalsPanel();
        layer.innerHTML = '<div class="asset-review-backdrop" data-team-close></div><section class="asset-team-modal" data-floating-content role="dialog" aria-modal="true" aria-label="团队管理">' +
            '<header><div><strong>'+(admin?'团队管理':'操作审批')+'</strong><span>'+esc(state.auth?.principal?.display_name || '')+' · '+esc(roleName(state.auth?.principal?.role))+'</span></div><button type="button" data-team-close aria-label="关闭">'+icon('x')+'</button></header>' +
            '<div class="asset-team-workspace"><nav aria-label="团队管理导航">'+tabs.map(([id,label,iconName])=>'<button type="button" data-team-tab="'+id+'" class="'+(state.teamTab===id?'active':'')+'">'+icon(iconName)+'<span>'+label+'</span>'+(id==='approvals'&&state.approvals.filter(item=>item.status==='pending').length?'<em>'+state.approvals.filter(item=>item.status==='pending').length+'</em>':'')+'</button>').join('')+'</nav><main>'+body+'</main></div>' +
            '<footer><span>所有变更都会记录操作人和时间</span><button type="button" data-team-close>完成</button></footer></section>';
        window.lucide?.createIcons();
    }

    async function loadTeamManagement(){
        const admin = state.auth?.principal?.role === 'admin';
        const [userData, teamData, approvalData] = await Promise.all([
            admin ? reviewJsonResponse(assetAuthApi().listUsers()) : Promise.resolve({users:[]}),
            admin ? reviewJsonResponse(assetAuthApi().listTeams()) : Promise.resolve({teams:[]}),
            reviewJsonResponse(assetAuthApi().listOperationApprovals(
                new URLSearchParams({status:state.approvalStatus}).toString(),
            )),
        ]);
        state.users = userData.users || [];
        state.teams = teamData.teams || [];
        state.approvals = approvalData.approvals || [];
        state.teamLoading = false;
        renderTeamManagement();
    }

    async function openTeamManagement(tab='', options={}){
        if(typeof options.host === 'boolean') state.parentModalHost = options.host;
        const user = state.auth?.principal;
        if(!user){ openAuth({host:state.parentModalHost}); return; }
        if(roleLevel(user.role) < roleLevel('reviewer')){
            if(state.parentModalHost){ notifyParentModalHost(false, true); state.parentModalHost = false; }
            toast('当前账户没有团队管理或审批权限', true);
            return;
        }
        const admin = user.role === 'admin';
        state.teamTab = tab || (admin ? 'members' : 'approvals');
        if(!admin && state.teamTab !== 'approvals') state.teamTab = 'approvals';
        state.teamLoading = true;
        const layer = ensureLayer();
        layer.className = 'asset-review-layer team-open';
        renderTeamManagement();
        if(state.parentModalHost) notifyParentModalHost(true);
        try { await loadTeamManagement(); }
        catch(error){ state.teamLoading=false;renderTeamManagement();toast(error.message,true); }
    }

    async function createTeamUser(form){
        const payload=Object.fromEntries(new FormData(form).entries());
        await reviewJsonResponse(assetAuthApi().createUser(payload));
        form.reset();await loadTeamManagement();toast('成员账户已创建');
    }

    async function updateTeamUser(button){
        const row=button.closest('[data-team-user-row]');
        if(!row)return;
        const payload={
            display_name:q('[data-team-user-display]',row)?.value||'',
            role:q('[data-team-user-role]',row)?.disabled?'':(q('[data-team-user-role]',row)?.value||''),
            status:q('[data-team-user-status]',row)?.disabled?'':(q('[data-team-user-status]',row)?.value||''),
            password:q('[data-team-user-password]',row)?.value||'',
        };
        await reviewJsonResponse(assetAuthApi().updateUser(button.dataset.teamUserSave, payload));
        await loadTeamManagement();toast('成员账户已更新');
    }

    async function deleteTeamUser(button){
        const userId=button.dataset.teamUserDelete||'';
        const userName=button.dataset.userName||'此成员';
        if(!userId||!confirm('确认删除成员账户“'+userName+'”？\n该成员的登录 Token 与团队成员关系会同步删除。'))return;
        await reviewJsonResponse(assetAuthApi().deleteUser(userId));
        await loadTeamManagement();toast('成员账户已删除');
    }

    async function createManagementTeam(form){
        if(form.dataset.submitting==='1')return;
        const button=form.querySelector('button[type="submit"]');
        const originalLabel=button?.textContent||'创建团队';
        form.dataset.submitting='1';
        if(button){button.disabled=true;button.textContent='创建中…';}
        const payload=Object.fromEntries(new FormData(form).entries());
        try{
            await reviewJsonResponse(assetAuthApi().createTeam(payload));
            form.reset();await loadTeamManagement();toast('团队已创建');
        }finally{
            form.dataset.submitting='';
            if(button){button.disabled=false;button.textContent=originalLabel;}
        }
    }

    async function deleteManagementTeam(button){
        const teamId=String(button.dataset.teamDelete || '').trim();
        const teamName=button.dataset.teamName || '此团队';
        if(!teamId || !confirm('确认删除团队“'+teamName+'”？\n仅删除没有项目或交付关联的团队，成员账户不会被删除。')) return;
        const password=window.prompt('请输入当前管理员密码以确认删除团队：');
        if(password===null) return;
        if(!String(password).trim()){ toast('请输入管理员密码', true); return; }
        button.disabled=true;
        try{
            await reviewJsonResponse(assetAuthApi().deleteTeam(teamId, {password}));
            await loadTeamManagement();
            toast('团队已删除');
        }finally{ button.disabled=false; }
    }

    async function addManagementTeamMember(form){
        const payload=Object.fromEntries(new FormData(form).entries());
        const teamId=form.dataset.teamMemberAdd||'';
        if(!payload.user_id)return;
        await reviewJsonResponse(assetAuthApi().updateTeamMember(teamId, {
            user_id:payload.user_id,
        }));
        await loadTeamManagement();toast('团队成员已添加');
    }

    async function saveManagementTeamMember(button){
        const [teamId,userId]=String(button.dataset.teamMemberSave||'').split('|');
        const row=button.closest('[data-team-member-row]');
        const role=q('[data-team-member-role]',row)?.value||'reviewer';
        await reviewJsonResponse(assetAuthApi().updateTeamMember(teamId, {user_id:userId,role}));
        await loadTeamManagement();toast('团队权限已更新');
    }

    async function removeManagementTeamMember(button){
        const [teamId,userId]=String(button.dataset.teamMemberRemove||'').split('|');
        if(!confirm('确定从团队中移除此成员？'))return;
        await reviewJsonResponse(assetAuthApi().deleteTeamMember(teamId, userId));
        await loadTeamManagement();toast('成员已从团队移除');
    }

    async function decideManagementApproval(button){
        const [approvalId,status]=String(button.dataset.teamApprovalDecision||'').split('|');
        const note=q('[data-team-approval-note="'+CSS.escape(approvalId)+'"]')?.value||'';
        await reviewJsonResponse(assetAuthApi().updateOperationApproval(approvalId, {status,note}));
        await loadTeamManagement();toast(status==='approved'?'操作已通过':'操作已拒绝');
    }

    async function submitAuth(form){
        // 只请求授权地址；凭据绝不经过本页（OIDC 授权码 + PKCE，无用户名/口令字段）。
        try {
            const data = await reviewJsonResponse(assetAuthApi().login());
            const authorizationUrl = String(data?.authorization_url || '');
            if(!authorizationUrl){
                throw new Error('认证服务未返回授权地址，已显式降级');
            }
            window.location.assign(authorizationUrl);
        } catch(error){
            const node = q('#assetAuthError');
            if(node) node.textContent = '认证服务未接入或不可达：' + error.message;
        }
    }

    async function createPcToken(){
        try {
            const data = await reviewJsonResponse(assetAuthApi().createToken({
                name:q('#reviewTokenName')?.value || 'PC 客户端',
                expires_days:365,
            }));
            const result = q('#reviewTokenResult');
            if(result) result.innerHTML = '<div class="token-result"><span>仅显示一次，请立即复制</span><code>'+esc(data.secret)+'</code><button data-review-copy="'+attr(data.secret)+'">'+icon('copy')+'复制</button></div>';
            window.lucide?.createIcons();
        } catch(error){ toast(error.message, true); }
    }

    function currentAsset(){
        try {
            if(typeof selectedRegistryAsset === 'function') return selectedRegistryAsset();
        } catch(_){}
        return null;
    }

    function normalizeReviewContext(asset, context={}){
        const versions = Array.isArray(asset?.versions) ? asset.versions : [];
        const links = Array.isArray(asset?.project_links) ? asset.project_links : [];
        const linked = links.length === 1 ? links[0] : {};
        return {
            asset_id:String(context.asset_id || asset?.id || '').trim(),
            asset_version_id:String(context.asset_version_id || asset?.asset_version_id || asset?.selected_version_id || asset?.latest_image_version_id || versions[0]?.id || '').trim(),
            project_id:String(context.project_id || asset?.project_id || linked.project_id || '').trim(),
            team_id:String(context.team_id || asset?.team_id || linked.team_id || '').trim(),
            canvas_id:String(context.canvas_id || asset?.canvas_id || '').trim(),
        };
    }

    async function performOpenReview(asset, context={}){
        const incoming = reviewContextValues(context);
        if(needsLogin()){
            pendingReviewContext = incoming;
            openAuth();
            return;
        }
        if(!asset?.id && incoming.asset_id){
            const loaded = await reviewJsonResponse(assetReviewApi().getRegistryAsset(incoming.asset_id));
            asset = loaded.asset || loaded;
        }
        asset = asset || currentAsset();
        if(!asset?.id){ toast('请先选择资产', true); return; }
        state.asset = {...asset};
        state.context = normalizeReviewContext(asset, incoming);
        if(!REVIEW_REQUIRED_KEYS.every(key => state.context[key])){
            toast('审阅需要固定的项目、团队和版本', true);
            return;
        }
        syncReviewUrl(state.context);
        try {
            const params = new URLSearchParams();
            Object.entries(state.context).forEach(([key,value]) => { if(value && key !== 'canvas_id') params.set(key, value); });
            const data = await reviewJsonResponse(assetReviewApi().listReviewSessions(params.toString()));
            state.sessions = data.sessions || [];
            if(!state.sessions.length && can('reviewer')){
                const created = await reviewJsonResponse(assetReviewApi().createReviewSession({
                    asset_id:asset.id,
                    title:(asset.name || '素材')+' 审阅',
                    visibility:'shared',
                    ...state.context,
                }));
                state.sessions = [created.session];
                state.detail = created;
                if(created.session?.version_media_url) state.asset.url = created.session.version_media_url;
                state.activeSessionId = created.session.id;
            } else {
                state.activeSessionId = state.sessions[0]?.id || '';
                state.detail = state.activeSessionId ? await reviewJsonResponse(assetReviewApi().getReviewSession(state.activeSessionId)) : null;
            }
            applyPinnedSessionMedia();
            renderReview();
        } catch(error){ toast(error.message, true); }
    }

    async function openReview(asset, context={}){
        const incoming = reviewContextValues(context);
        const key = reviewContextKey(incoming);
        if(key && key === reviewOpenKey && reviewOpenPromise) return reviewOpenPromise;
        if(key && key === reviewContextKey(state.context) && state.detail && q('#assetReviewLayer')?.classList.contains('review-open')) return;
        const task = performOpenReview(asset, incoming);
        if(!key) return task;
        reviewOpenKey = key;
        reviewOpenPromise = task;
        try {
            return await task;
        } finally {
            if(reviewOpenPromise === task) reviewOpenPromise = null;
        }
    }

    function applyPinnedSessionMedia(){
        const pinned = state.detail?.session?.version_media_url;
        if(pinned && state.asset) state.asset.url = pinned;
    }

    function formatTimecode(ms){
        if(ms === null || ms === undefined) return '';
        const total = Math.max(0, Number(ms || 0));
        const hours = Math.floor(total / 3600000);
        const minutes = Math.floor((total % 3600000) / 60000);
        const seconds = Math.floor((total % 60000) / 1000);
        const frames = Math.floor((total % 1000) / 40);
        return [hours, minutes, seconds].map(value => String(value).padStart(2,'0')).join(':') + ':' + String(frames).padStart(2,'0');
    }

    function annotationSvg(annotation, draft=false){
        const geometry = annotation?.geometry || {};
        const style = annotation?.style || {};
        const stroke = style.color || (draft ? '#ffb020' : '#ff4d67');
        const width = Number(style.width || 4);
        const x = Number(geometry.x || 0) * 1000;
        const y = Number(geometry.y || 0) * 1000;
        const x2 = Number(geometry.x2 ?? geometry.x ?? 0) * 1000;
        const y2 = Number(geometry.y2 ?? geometry.y ?? 0) * 1000;
        const w = Number(geometry.width || 0) * 1000;
        const h = Number(geometry.height || 0) * 1000;
        if(annotation.kind === 'pin') return '<g><circle cx="'+x+'" cy="'+y+'" r="18" fill="'+stroke+'" opacity=".95"/><circle cx="'+x+'" cy="'+y+'" r="7" fill="white"/></g>';
        if(annotation.kind === 'rect') return '<rect x="'+x+'" y="'+y+'" width="'+w+'" height="'+h+'" fill="none" stroke="'+stroke+'" stroke-width="'+width+'" vector-effect="non-scaling-stroke"/>';
        if(annotation.kind === 'ellipse') return '<ellipse cx="'+(x+w/2)+'" cy="'+(y+h/2)+'" rx="'+Math.abs(w/2)+'" ry="'+Math.abs(h/2)+'" fill="none" stroke="'+stroke+'" stroke-width="'+width+'" vector-effect="non-scaling-stroke"/>';
        if(annotation.kind === 'arrow') return '<line x1="'+x+'" y1="'+y+'" x2="'+x2+'" y2="'+y2+'" stroke="'+stroke+'" stroke-width="'+width+'" marker-end="url(#reviewArrow)" vector-effect="non-scaling-stroke"/>';
        if(annotation.kind === 'freehand'){
            const points = (geometry.points || []).map(point => (Number(point.x)*1000)+','+(Number(point.y)*1000)).join(' ');
            return '<polyline points="'+attr(points)+'" fill="none" stroke="'+stroke+'" stroke-width="'+width+'" stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>';
        }
        return '';
    }

    function mediaMarkup(asset){
        const url = attr(asset?.url || asset?.public_url || '');
        if(asset?.kind === 'video') return '<video id="reviewMedia" src="'+url+'" controls playsinline preload="metadata"></video>';
        if(asset?.kind === 'audio') return '<div class="review-audio">'+icon('audio-lines',42)+'<audio id="reviewMedia" src="'+url+'" controls preload="metadata"></audio></div>';
        return '<img id="reviewMedia" src="'+url+'" alt="'+attr(asset?.name || 'asset')+'">';
    }

    function annotationLayer(){
        const annotations = (state.detail?.comments || []).flatMap(comment => comment.annotations || []);
        const all = state.draftAnnotation ? [...annotations, {...state.draftAnnotation, _draft:true}] : annotations;
        return '<svg id="reviewAnnotationCanvas" class="review-annotation-canvas" viewBox="0 0 1000 1000" preserveAspectRatio="none">' +
            '<defs><marker id="reviewArrow" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto"><path d="M0,0 L12,6 L0,12 z" fill="#ff4d67"/></marker></defs>' +
            all.map(value => annotationSvg(value, value._draft)).join('') + '</svg>';
    }

    function commentMarkup(comment){
        const time = comment.timecode_ms !== null && comment.timecode_ms !== undefined
            ? '<button data-review-seek="'+Number(comment.timecode_ms)+'">'+icon('timer')+formatTimecode(comment.timecode_ms)+'</button>' : '';
        const range = comment.in_ms !== null && comment.in_ms !== undefined
            ? '<span>'+formatTimecode(comment.in_ms)+' – '+formatTimecode(comment.out_ms)+'</span>' : '';
        const resolved = comment.status === 'resolved';
        return '<article class="review-comment '+(resolved?'resolved':'')+'" style="--reply:'+ (comment.parent_id ? 1 : 0) +'">' +
            '<header><strong>'+esc(comment.author_name || '访客')+'</strong><time>'+new Date(Number(comment.created_at || 0)).toLocaleString()+'</time></header>' +
            '<p>'+esc(comment.body || (comment.annotations?.length ? '图形批注' : '')).replace(/\n/g,'<br>')+'</p>' +
            '<footer>'+time+range+(comment.annotations?.length?'<span>'+icon('pencil-line')+comment.annotations.length+' 个标记</span>':'') +
            (can('reviewer')?'<button data-review-resolve="'+attr(comment.id)+'" data-resolved="'+(resolved?'1':'0')+'">'+icon(resolved?'rotate-ccw':'check')+(resolved?'重新打开':'解决')+'</button>':'')+
            '<button data-review-reply="'+attr(comment.id)+'">'+icon('reply')+'回复</button></footer></article>';
    }

    function approvalMarkup(){
        const latest = state.detail?.approvals?.[0];
        const status = state.detail?.session?.status || 'open';
        return '<section class="review-approval"><div><strong>版本审批</strong><span class="review-status '+attr(status)+'">'+esc(status)+'</span></div>' +
            '<p>'+(latest ? esc((latest.reviewer_name || '')+'：'+(latest.note || latest.status)) : '尚无审批记录')+'</p>' +
            (can('reviewer')?'<div><button data-review-approval="changes_requested">'+icon('message-square-warning')+'需修改</button><button data-review-approval="approved">'+icon('badge-check')+'通过</button>'+(can('editor')?'<button data-review-approval="locked">'+icon('lock-keyhole')+'锁定</button>':'')+'</div>':'')+'</section>';
    }

    function injectDeliveryAction(){
        const session = state.detail?.session;
        const host = q('.review-drawer-head > div:last-child');
        if(!host || !session?.delivery_eligible || !can('editor') || host.querySelector('[data-review-delivery]')) return;
        const button = document.createElement('button');
        button.type = 'button';
        button.dataset.reviewDelivery = '1';
        button.title = '导出精确版本交付包';
        button.innerHTML = icon('package-check') + '交付';
        host.insertBefore(button, host.lastElementChild);
        window.lucide?.createIcons();
    }

    async function exportDelivery(){
        const sessionId = state.activeSessionId;
        if(!sessionId) return;
        try {
            const created = await reviewJsonResponse(assetReviewApi().createReviewDelivery(sessionId, {
                title:(state.asset?.name || 'asset')+' delivery',
            }));
            const deliveryId = created.delivery?.id;
            if(!deliveryId) throw new Error('交付清单创建失败');
            const response = await assetReviewApi().exportReviewDelivery(deliveryId);
            if(response.status === 401) openAuth();
            if(!response.ok){ const body = await response.json().catch(()=>({})); throw new Error(body.detail?.message || body.detail || '交付包导出失败'); }
            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = response.headers.get('content-disposition')?.match(/filename="?([^";]+)"?/i)?.[1] || 'delivery.zip';
            link.hidden = true;
            const downloadHost = q('.asset-review-drawer[data-floating-content]') || document.body;
            downloadHost.appendChild(link);
            link.click();
            link.remove();
            window.setTimeout(() => URL.revokeObjectURL(url), 1000);
            toast('交付包已导出');
        } catch(error){ toast(error.message, true); }
    }

    function renderReview(){
        const layer = ensureLayer();
        const session = state.detail?.session;
        layer.className = 'asset-review-layer review-open';
        if(!session){
            layer.innerHTML = '<div class="asset-review-backdrop" data-review-close></div><section class="asset-review-drawer" data-floating-content><header><strong>审阅批注</strong><button data-review-close>'+icon('x')+'</button></header><div class="review-empty">暂无审阅会话，当前账号只有查看权限。</div></section>';
            window.lucide?.createIcons();
            return;
        }
        const comments = state.detail?.comments || [];
        layer.innerHTML = '<div class="asset-review-backdrop" data-review-close></div><section class="asset-review-drawer" data-floating-content>' +
            '<header class="review-drawer-head"><div><strong>'+esc(session.title)+'</strong><span>'+esc(state.asset?.name || session.asset_name || '')+'</span></div>' +
            '<div><select id="reviewSessionSelect">'+state.sessions.map(item=>'<option value="'+attr(item.id)+'" '+(item.id===session.id?'selected':'')+'>'+esc(item.title)+'</option>').join('')+'</select>' +
            (can('editor')?'<button data-review-share>'+icon('share-2')+'分享</button>':'')+'<button data-review-close>'+icon('x')+'</button></div></header>' +
            '<div class="review-workspace"><section class="review-stage-wrap"><div class="review-stage" data-review-draw-surface>' +
            mediaMarkup(state.asset || session, session) + (state.asset?.kind !== 'audio' ? annotationLayer() : '') +
            '<div class="review-watermark">INFINITE CANVAS · REVIEW</div></div>' +
            '<div class="review-drawbar"><span>批注工具</span>' +
            ['pin','rect','ellipse','arrow','freehand'].map(mode=>'<button class="'+(state.drawMode===mode?'active':'')+'" data-review-tool="'+mode+'">'+icon({pin:'map-pin',rect:'square',ellipse:'circle',arrow:'move-up-right',freehand:'pencil'}[mode])+'</button>').join('') +
            '<button data-review-clear-draft>'+icon('eraser')+'清除标记</button>' +
            (['video','audio'].includes(state.asset?.kind)?'<button data-review-capture-time>'+icon('timer')+'取当前时间点</button>':'') +
            (state.timecodeMs!==null?'<em>'+formatTimecode(state.timecodeMs)+'</em>':'')+'</div></section>' +
            '<aside class="review-sidebar">'+approvalMarkup()+'<section class="review-thread"><header><strong>评论</strong><span>'+comments.length+'</span></header>' +
            '<div class="review-comments">'+(comments.map(commentMarkup).join('') || '<div class="review-empty">还没有评论</div>')+'</div>' +
            (can('reviewer')?'<form id="reviewCommentForm"><input id="reviewReplyId" type="hidden"><div id="reviewReplyHint"></div><textarea name="body" placeholder="写下修改意见，或直接在画面上标记…"></textarea><div><label><input name="visibility" type="checkbox" value="internal"> 仅内部可见</label><button class="review-primary" type="submit">'+icon('send')+'发布评论</button></div></form>':'') +
            '</section></aside></div></section>';
        window.lucide?.createIcons();
        injectDeliveryAction();
    }

    async function loadSession(sessionId){
        state.activeSessionId = sessionId;
        state.detail = await reviewJsonResponse(assetReviewApi().getReviewSession(sessionId));
        applyPinnedSessionMedia();
        state.draftAnnotation = null;
        renderReview();
    }

    function refreshAnnotationSvg(){
        const svg = q('#reviewAnnotationCanvas');
        if(!svg) return;
        const annotations = (state.detail?.comments || []).flatMap(comment => comment.annotations || []);
        const all = state.draftAnnotation ? [...annotations, {...state.draftAnnotation, _draft:true}] : annotations;
        svg.innerHTML = '<defs><marker id="reviewArrow" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto"><path d="M0,0 L12,6 L0,12 z" fill="#ff4d67"/></marker></defs>' +
            all.map(value => annotationSvg(value, value._draft)).join('');
    }

    function pointerPosition(event, surface){
        const rect = surface.getBoundingClientRect();
        return {
            x:Math.max(0,Math.min(1,(event.clientX-rect.left)/Math.max(1,rect.width))),
            y:Math.max(0,Math.min(1,(event.clientY-rect.top)/Math.max(1,rect.height)))
        };
    }

    function beginDraw(event, surface){
        if(!can('reviewer') || state.asset?.kind === 'audio' || ['VIDEO','AUDIO','BUTTON'].includes(event.target?.tagName)) return;
        const point = pointerPosition(event, surface);
        state.drawStart = point;
        state.drawPoints = [point];
        if(state.drawMode === 'pin'){
            state.draftAnnotation = {kind:'pin',geometry:point,style:{color:'#ffb020',width:4}};
            refreshAnnotationSvg();
            state.drawStart = null;
        } else {
            surface.setPointerCapture?.(event.pointerId);
        }
    }

    function moveDraw(event, surface){
        if(!state.drawStart) return;
        const point = pointerPosition(event, surface);
        if(state.drawMode === 'freehand') state.drawPoints.push(point);
        const start = state.drawStart;
        if(state.drawMode === 'rect' || state.drawMode === 'ellipse'){
            state.draftAnnotation = {
                kind:state.drawMode,
                geometry:{x:Math.min(start.x,point.x),y:Math.min(start.y,point.y),width:Math.abs(point.x-start.x),height:Math.abs(point.y-start.y)},
                style:{color:'#ffb020',width:4}
            };
        } else if(state.drawMode === 'arrow'){
            state.draftAnnotation = {kind:'arrow',geometry:{x:start.x,y:start.y,x2:point.x,y2:point.y},style:{color:'#ffb020',width:4}};
        } else if(state.drawMode === 'freehand'){
            state.draftAnnotation = {kind:'freehand',geometry:{points:state.drawPoints.slice(-5000)},style:{color:'#ffb020',width:4}};
        }
        refreshAnnotationSvg();
    }

    function endDraw(event, surface){
        if(!state.drawStart) return;
        moveDraw(event, surface);
        state.drawStart = null;
        state.drawPoints = [];
    }

    async function submitComment(form){
        const body = q('textarea[name="body"]',form)?.value || '';
        const visibility = q('input[name="visibility"]',form)?.checked ? 'internal' : 'shared';
        const payload = {
            body,
            parent_id:q('#reviewReplyId')?.value || '',
            visibility,
            timecode_ms:state.timecodeMs,
            annotations:state.draftAnnotation ? [state.draftAnnotation] : [],
        };
        try {
            await reviewJsonResponse(assetReviewApi().createReviewComment(state.activeSessionId, payload));
            state.timecodeMs = null;
            state.draftAnnotation = null;
            await loadSession(state.activeSessionId);
            toast('评论已发布');
        } catch(error){ toast(error.message,true); }
    }

    async function updateApproval(status){
        const note = window.prompt(status === 'approved' ? '审批说明（可选）' : status === 'locked' ? '锁定说明（可选）' : '请说明需要修改的内容') ?? '';
        try {
            await reviewJsonResponse(assetReviewApi().updateReviewApproval(state.activeSessionId, {status,note}));
            await loadSession(state.activeSessionId);
            toast('审批状态已更新');
        } catch(error){ toast(error.message,true); }
    }

    function shareAssetIds(){
        try {
            if(typeof registrySelectedIds !== 'undefined' && registrySelectedIds?.size) return [...registrySelectedIds];
        } catch(_){}
        return state.asset?.id ? [state.asset.id] : [];
    }

    function openShare(){
        const assetIds = shareAssetIds();
        if(!assetIds.length){ toast('请先选择资产',true); return; }
        const activeSession = state.detail?.session;
        const sessionId = activeSession && assetIds.includes(activeSession.asset_id) ? activeSession.session_id : '';
        const layer = ensureLayer();
        layer.className = 'asset-review-layer share-open';
        layer.innerHTML = '<div class="asset-review-backdrop" data-review-close></div><section class="asset-share-modal" data-floating-content><header><div><strong>创建审阅分享</strong><span>'+assetIds.length+' 个资产</span></div><button data-review-close>'+icon('x')+'</button></header>' +
            '<form id="assetShareForm"><label>分享标题<input name="title" value="'+attr((state.asset?.name || '素材')+' 审阅')+'" maxlength="160"></label>' +
            '<div class="share-form-grid"><label>访问密码<input name="password" type="password" placeholder="留空则无需密码"></label><label>有效天数<input name="days" type="number" value="7" min="1" max="3650"></label>' +
            '<label>最多访问次数<input name="max_access_count" type="number" value="0" min="0"><small>0 表示不限</small></label><label>水印文字<input name="watermark_text" value="仅供审阅"></label></div>' +
            '<div class="share-permissions"><label><input name="can_comment" type="checkbox" checked> 允许评论与审批</label><label><input name="can_download" type="checkbox"> 允许下载原文件</label></div>' +
            '<input name="asset_ids" type="hidden" value="'+attr(assetIds.join(','))+'"><input name="session_id" type="hidden" value="'+attr(sessionId)+'"><button class="review-primary" type="submit">'+icon('link')+'创建链接</button><div id="shareCreateResult"></div></form></section>';
        window.lucide?.createIcons();
    }

    async function submitShare(form){
        const values = new FormData(form);
        const days = Math.max(1,Number(values.get('days')||7));
        const payload = {
            asset_ids:String(values.get('asset_ids')||'').split(',').filter(Boolean),
            session_id:String(values.get('session_id')||'')||null,
            title:String(values.get('title')||'素材审阅'),
            password:String(values.get('password')||''),
            expires_at:Date.now()+days*86400000,
            max_access_count:Number(values.get('max_access_count')||0),
            can_comment:values.has('can_comment'),
            can_download:values.has('can_download'),
            watermark_text:String(values.get('watermark_text')||''),
        };
        try {
            const data = await reviewJsonResponse(assetReviewApi().createReviewShare(payload));
            const token = String(data.token || '');
            if (!token) throw new Error('分享未返回一次性令牌，不能生成访问链接');
            const url = location.origin + '/share/' + encodeURIComponent(token);
            const result = q('#shareCreateResult');
            if(result) result.innerHTML = '<div class="share-result"><span>链接已创建，密钥不会再次显示</span><code>'+esc(url)+'</code><button type="button" data-review-copy="'+attr(url)+'">'+icon('copy')+'复制链接</button></div>';
            window.lucide?.createIcons();
        } catch(error){ toast(error.message,true); }
    }

    async function handleClick(event){
        const target = event.target;
        if(target.closest?.('[data-review-open]')){ await openReview(); return; }
        if(target.closest?.('[data-review-share-selected]')){ openShare(); return; }
        if(target.closest?.('#assetAccountBtn')){ openAuth(); return; }
        if(target.closest?.('#assetTeamBtn')){ await openTeamManagement(); return; }
        if(target.closest?.('[data-team-close]')){ closeLayer(); return; }
        const teamTab=target.closest?.('[data-team-tab]');
        if(teamTab){ state.teamTab=teamTab.dataset.teamTab||'approvals';renderTeamManagement();return; }
        const teamDelete=target.closest?.('[data-team-delete]');
        if(teamDelete){ await deleteManagementTeam(teamDelete);return; }
        const userSave=target.closest?.('[data-team-user-save]');
        if(userSave){ await updateTeamUser(userSave);return; }
        const userDelete=target.closest?.('[data-team-user-delete]');
        if(userDelete){ await deleteTeamUser(userDelete);return; }
        const memberSave=target.closest?.('[data-team-member-save]');
        if(memberSave){ await saveManagementTeamMember(memberSave);return; }
        const memberRemove=target.closest?.('[data-team-member-remove]');
        if(memberRemove){ await removeManagementTeamMember(memberRemove);return; }
        const approvalDecision=target.closest?.('[data-team-approval-decision]');
        if(approvalDecision){ await decideManagementApproval(approvalDecision);return; }
        if(target.closest?.('[data-review-close]')){ closeReviewLayer(); return; }
        if(target.closest?.('[data-review-token-create]')){ await createPcToken(); return; }
        if(target.closest?.('[data-review-logout]')){
            await reviewJsonResponse(assetAuthApi().logout()).catch(()=>{});
            closeReviewSocket(true);
            closeLayer({releaseHost:false}); await loadAuth(); return;
        }
        const copy = target.closest?.('[data-review-copy]');
        if(copy){ await navigator.clipboard.writeText(copy.dataset.reviewCopy||''); toast('已复制'); return; }
        const tool = target.closest?.('[data-review-tool]');
        if(tool){ state.drawMode=tool.dataset.reviewTool||'pin'; qa('[data-review-tool]').forEach(node=>node.classList.toggle('active',node===tool)); return; }
        if(target.closest?.('[data-review-clear-draft]')){ state.draftAnnotation=null;refreshAnnotationSvg();return; }
        if(target.closest?.('[data-review-capture-time]')){
            const media=q('#reviewMedia');state.timecodeMs=Math.round(Number(media?.currentTime||0)*1000);renderReview();return;
        }
        const seek=target.closest?.('[data-review-seek]');
        if(seek){ const media=q('#reviewMedia');if(media){media.currentTime=Number(seek.dataset.reviewSeek||0)/1000;media.play?.();}return; }
        const resolve=target.closest?.('[data-review-resolve]');
        if(resolve){
            await reviewJsonResponse(assetReviewApi().updateReviewComment(
                resolve.dataset.reviewResolve,
                {resolved:resolve.dataset.resolved!=='1'},
            )); await loadSession(state.activeSessionId); return;
        }
        const reply=target.closest?.('[data-review-reply]');
        if(reply){ q('#reviewReplyId').value=reply.dataset.reviewReply||'';q('#reviewReplyHint').textContent='正在回复此评论 · 点击取消';return; }
        const approval=target.closest?.('[data-review-approval]');
        if(approval){ await updateApproval(approval.dataset.reviewApproval);return; }
        if(target.closest?.('[data-review-delivery]')){ await exportDelivery();return; }
        if(target.closest?.('[data-review-share]')){ openShare();return; }
    }

    async function handleSubmit(event){
        if(event.target.id === 'assetAuthForm'){ event.preventDefault();await submitAuth(event.target);return; }
        if(event.target.id === 'reviewCommentForm'){ event.preventDefault();await submitComment(event.target);return; }
        if(event.target.id === 'assetShareForm'){ event.preventDefault();await submitShare(event.target);return; }
        if(event.target.id === 'assetUserCreateForm'){ event.preventDefault();await createAdminUser(event.target);return; }
        if(event.target.id === 'assetTeamCreateForm'){ event.preventDefault();await createAdminTeam(event.target);return; }
        if(event.target.id === 'assetTeamMemberForm'){ event.preventDefault();await setAdminTeamMember(event.target);return; }
        if(event.target.id === 'teamUserCreateForm'){ event.preventDefault();await createTeamUser(event.target);return; }
        if(event.target.id === 'teamCreateForm'){ event.preventDefault();await createManagementTeam(event.target);return; }
        if(event.target.matches?.('[data-team-member-add]')){ event.preventDefault();await addManagementTeamMember(event.target);return; }
    }

    function connectReviewSocket(){
        clearTimeout(state.wsRetryTimer);
        if(state.ws || state.wsBlocked || needsLogin()) return;
        const protocol=location.protocol==='https:'?'wss:':'ws:';
        let socket;
        try { socket=new WebSocket(protocol+'//'+location.host+'/ws/stats?client_id=asset-review-'+Math.random().toString(36).slice(2)); }
        catch(_){ state.wsRetryTimer=setTimeout(connectReviewSocket, Math.min(30000, 4000 * 2 ** Math.min(state.wsRetryCount++, 3))); return; }
        state.ws=socket;
        socket.onopen=()=>{ state.wsRetryCount=0; };
        socket.onmessage=event=>{
            try {
                const message=JSON.parse(event.data);
                if(message.type==='asset_review_updated' && state.activeSessionId && (!message.session_id || message.session_id===state.activeSessionId)){
                    clearTimeout(state._refreshTimer);
                    state._refreshTimer=setTimeout(()=>loadSession(state.activeSessionId).catch(()=>{}),300);
                }
            } catch(_){}
        };
        socket.onclose=event=>{
            if(state.ws!==socket) return;
            state.ws=null;
            // A rejected handshake is often surfaced as browser code 1006,
            // so authenticated pages must never spin on an opaque 403.
            if(needsLogin() || [4001,4003,4401,4403].includes(Number(event.code))){ state.wsBlocked=true; return; }
            const delay=Math.min(30000, 4000 * 2 ** Math.min(state.wsRetryCount++, 3));
            state.wsRetryTimer=setTimeout(connectReviewSocket, delay);
        };
    }

    function closeReviewSocket(blocked=false){
        clearTimeout(state.wsRetryTimer);
        state.wsRetryTimer=null;
        state.wsBlocked=blocked;
        const socket=state.ws;
        state.ws=null;
        if(socket){ try{ socket.close(); }catch(_){} }
    }

    function bindDrawing(){
        document.addEventListener('pointerdown',event=>{
            const surface=event.target.closest?.('[data-review-draw-surface]');
            if(surface) beginDraw(event,surface);
        });
        document.addEventListener('pointermove',event=>{
            const surface=q('[data-review-draw-surface]');
            if(surface && state.drawStart) moveDraw(event,surface);
        });
        document.addEventListener('pointerup',event=>{
            const surface=q('[data-review-draw-surface]');
            if(surface && state.drawStart) endDraw(event,surface);
        });
    }

    async function resumePendingReview(){
        if(!reviewReady || !pendingReviewContext) return;
        if(needsLogin()) return;
        const context = pendingReviewContext;
        pendingReviewContext = null;
        await openReview(null, context);
    }

    async function init(){
        document.addEventListener('click',event=>handleClick(event).catch(error=>toast(error.message,true)));
        document.addEventListener('submit',event=>handleSubmit(event).catch(error=>toast(error.message,true)));
        document.addEventListener('change',event=>{
            if(event.target.id==='reviewSessionSelect') loadSession(event.target.value).catch(error=>toast(error.message,true));
            if(event.target.id==='teamApprovalFilter'){
                state.approvalStatus=event.target.value||'pending';
                state.teamLoading=true;
                renderTeamManagement();
                loadTeamManagement().catch(error=>{state.teamLoading=false;renderTeamManagement();toast(error.message,true);});
            }
        });
        window.addEventListener('asset-auth-needed',()=>{ closeReviewSocket(true); openAuth(); });
        window.addEventListener('message',event=>{
            if(event.origin && event.origin!==location.origin) return;
            if(event.data?.type==='asset-open-account') openAuth({host:Boolean(event.data.host)});
            if(event.data?.type==='asset-open-team') openTeamManagement(event.data.tab||'', {host:Boolean(event.data.host)}).catch(error=>toast(error.message,true));
            if(event.data?.type==='asset-modal-host-dismiss'){ closeLayer(); return; }
            if(event.data?.type==='asset-modal-host-hidden'){
                document.body.classList.remove('asset-modal-host');
                try {
                    if(parent !== window) parent.postMessage({type:'asset-modal-host-content-hidden'}, location.origin);
                } catch(_) {}
                return;
            }
            if(event.data?.type==='asset-review-close'){ closeReviewLayer(); return; }
            if(event.data?.type==='asset-open-review'){
                const context = event.data.context || event.data;
                if(!reviewReady){ pendingReviewContext = context; return; }
                if(needsLogin()){ pendingReviewContext = context; openAuth(); return; }
                openReview(event.data.asset || null, context).catch(error=>toast(error.message,true));
            }
        });
        bindDrawing();
        await loadAuth();
        reviewReady = true;
        if(!pendingReviewContext) pendingReviewContext = reviewContextFromUrl();
        await resumePendingReview();
        connectReviewSocket();
    }

    window.AssetReview = {
        open: context => openReview(context?.asset || null, context || {}),
        openTeamManagement: (tab = '') => openTeamManagement(tab),
    };
    if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',init);
    else init();
})();
