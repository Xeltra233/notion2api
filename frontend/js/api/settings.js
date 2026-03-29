window.NotionAI = window.NotionAI || {};
window.NotionAI.API = window.NotionAI.API || {};

window.NotionAI.API.Settings = {
    _oauthCallbackCache: null,
    _runtimeSecretsVisible: false,
    _runtimeAdvancedVisible: false,
    _expandedActionHistoryKeys: {},

    getActionHistoryFilters() {
        return {
            account: document.getElementById('adminActionHistoryAccountFilter')?.value.trim() || '',
            type: document.getElementById('adminActionHistoryTypeFilter')?.value || '',
            status: document.getElementById('adminActionHistoryStatusFilter')?.value || '',
            failureCategory: document.getElementById('adminActionHistoryFailureFilter')?.value || '',
            reauthOnly: document.getElementById('adminActionHistoryReauthOnly')?.checked || false,
        };
    },

    getAdminFilters() {
        return {
            q: document.getElementById('adminSearchInput')?.value.trim() || '',
            state: document.getElementById('adminStateFilterInput')?.value.trim() || '',
            plan_category: document.getElementById('adminPlanFilterInput')?.value.trim() || '',
            enabled: document.getElementById('adminEnabledFilterInput')?.value || '',
            sort_by: document.getElementById('adminSortByInput')?.value || 'updated_at',
            sort_order: document.getElementById('adminSortOrderInput')?.value || 'desc',
            page: document.getElementById('adminPageInput')?.value || '1',
            page_size: document.getElementById('adminPageSizeInput')?.value || '20',
        };
    },

    getUsageFilters() {
        const limit = Number(document.getElementById('adminUsageLimitInput')?.value || 8);
        return {
            start_ts: document.getElementById('adminUsageStartTsInput')?.value.trim() || '',
            end_ts: document.getElementById('adminUsageEndTsInput')?.value.trim() || '',
            model: document.getElementById('adminUsageModelInput')?.value.trim() || '',
            account_id: document.getElementById('adminUsageAccountInput')?.value.trim() || '',
            request_type: document.getElementById('adminUsageRequestTypeInput')?.value || '',
            limit: String(Math.min(100, Math.max(1, Number.isFinite(limit) ? limit : 8))),
            offset: '0',
        };
    },

    clearUsageFilters() {
        ['adminUsageStartTsInput', 'adminUsageEndTsInput', 'adminUsageModelInput', 'adminUsageAccountInput'].forEach((id) => {
            const input = document.getElementById(id);
            if (input) {
                input.value = '';
            }
        });
        const requestType = document.getElementById('adminUsageRequestTypeInput');
        if (requestType) {
            requestType.value = '';
        }
        const limit = document.getElementById('adminUsageLimitInput');
        if (limit) {
            limit.value = '8';
        }
    },

    applyPendingHydrationFilter() {
        const stateFilter = document.getElementById('adminStateFilterInput');
        if (stateFilter) {
            stateFilter.value = 'workspace_creation_pending';
        }
        this.refreshAdminPanel('已筛选为待补全账号。');
    },

    applyHydrationDueFilter() {
        const stateFilter = document.getElementById('adminStateFilterInput');
        if (stateFilter) {
            stateFilter.value = 'workspace_hydration_due';
        }
        this.refreshAdminPanel('已筛选为补全到期账号。');
    },

    changeAdminPage(delta) {
        const input = document.getElementById('adminPageInput');
        if (!input) {
            return;
        }
        const current = Number(input.value || 1);
        input.value = String(Math.max(1, current + delta));
        this.refreshAdminPanel(`已加载第 ${input.value} 页。`);
    },

    clearAdminFilters() {
        ['adminSearchInput', 'adminStateFilterInput', 'adminPlanFilterInput'].forEach((id) => {
            const input = document.getElementById(id);
            if (input) {
                input.value = '';
            }
        });
        const enabled = document.getElementById('adminEnabledFilterInput');
        if (enabled) {
            enabled.value = '';
        }
        const sortBy = document.getElementById('adminSortByInput');
        if (sortBy) {
            sortBy.value = 'updated_at';
        }
        const sortOrder = document.getElementById('adminSortOrderInput');
        if (sortOrder) {
            sortOrder.value = 'desc';
        }
        const page = document.getElementById('adminPageInput');
        if (page) {
            page.value = '1';
        }
        const pageSize = document.getElementById('adminPageSizeInput');
        if (pageSize) {
            pageSize.value = '20';
        }
        const actionAccount = document.getElementById('adminActionHistoryAccountFilter');
        if (actionAccount) {
            actionAccount.value = '';
        }
        const actionType = document.getElementById('adminActionHistoryTypeFilter');
        if (actionType) {
            actionType.value = '';
        }
        const actionStatus = document.getElementById('adminActionHistoryStatusFilter');
        if (actionStatus) {
            actionStatus.value = '';
        }
        const actionFailure = document.getElementById('adminActionHistoryFailureFilter');
        if (actionFailure) {
            actionFailure.value = '';
        }
        const reauthOnly = document.getElementById('adminActionHistoryReauthOnly');
        if (reauthOnly) {
            reauthOnly.checked = false;
        }
        this.clearUsageFilters();
    },

    applyQuickFilter(type) {
        this.clearAdminFilters();
        if (type === 'probe_failures') {
            this.refreshAdminPanel(`已应用快捷筛选：${type}。`);
            return;
        }
        if (type === 'invalid') {
            document.getElementById('adminStateFilterInput').value = 'invalid';
        } else if (type === 'needs_refresh') {
            document.getElementById('adminStateFilterInput').value = 'needs_refresh';
        } else if (type === 'no_workspace') {
            document.getElementById('adminStateFilterInput').value = 'no_workspace';
        } else if (type === 'education') {
            document.getElementById('adminPlanFilterInput').value = 'education';
        } else if (type === 'usable') {
            document.getElementById('adminStateFilterInput').value = 'active';
            document.getElementById('adminEnabledFilterInput').value = 'true';
        }
        this.refreshAdminPanel(`已应用快捷筛选：${type}。`);
    },

    applyAlertFilter(type) {
        this.clearAdminFilters();
        if (type === 'oauth_expired' || type === 'needs_refresh' || type === 'invalid' || type === 'no_workspace' || type === 'workspace_creation_pending') {
            document.getElementById('adminStateFilterInput').value = type;
            this.refreshAdminPanel(`已应用告警筛选：${type}。`);
            return;
        }
        if (type === 'action_failures') {
            const status = document.getElementById('adminActionHistoryStatusFilter');
            if (status) {
                status.value = 'failed';
            }
            this.renderActionHistory(this._lastAdminSnapshot || {});
            this.setAdminNotice('操作历史已筛选为失败动作。');
            return;
        }
        if (type === 'action_reauth_required') {
            const status = document.getElementById('adminActionHistoryStatusFilter');
            const reauth = document.getElementById('adminActionHistoryReauthOnly');
            if (status) {
                status.value = 'failed';
            }
            if (reauth) {
                reauth.checked = true;
            }
            this.renderActionHistory(this._lastAdminSnapshot || {});
            this.setAdminNotice('操作历史已筛选为需要重新授权的动作。');
            return;
        }
        if (type === 'action_rate_limited') {
            const status = document.getElementById('adminActionHistoryStatusFilter');
            const failure = document.getElementById('adminActionHistoryFailureFilter');
            if (status) {
                status.value = 'failed';
            }
            if (failure) {
                failure.value = 'rate_limited';
            }
            this.renderActionHistory(this._lastAdminSnapshot || {});
            this.setAdminNotice('操作历史已筛选为限流动作。');
            return;
        }
        this.refreshAdminPanel(`已应用告警筛选：${type}。`);
    },

    getOAuthCallbackParams() {
        if (this._oauthCallbackCache) {
            return this._oauthCallbackCache;
        }
        return this.parseOAuthCallbackUrl(window.location.href);
    },

    parseOAuthCallbackUrl(rawUrl) {
        const fallback = {
            token_v2: '',
            user_id: '',
            space_id: '',
            user_email: '',
            access_token: '',
            refresh_token: '',
            expires_at: '',
            state: '',
            consumed: false,
            autoFinalized: false,
            detected: false
        };

        let parsedUrl;
        try {
            parsedUrl = new URL(rawUrl || window.location.href, window.location.origin);
        } catch (error) {
            return fallback;
        }

        const params = parsedUrl.searchParams;
        const hash = String(parsedUrl.hash || '').replace(/^#/, '');
        const hashParams = new URLSearchParams(hash);
        const pick = (key) => params.get(key) || hashParams.get(key) || '';

        this._oauthCallbackCache = {
            token_v2: pick('token_v2'),
            user_id: pick('user_id'),
            space_id: pick('space_id'),
            user_email: pick('user_email') || pick('email'),
            access_token: pick('access_token'),
            refresh_token: pick('refresh_token'),
            expires_at: pick('expires_at'),
            state: pick('state'),
            consumed: false,
            autoFinalized: false,
            detected: Boolean(
                pick('token_v2') || pick('user_id') || pick('access_token')
            )
        };
        return this._oauthCallbackCache;
    },

    fillOAuthFinalizeForm(payload = {}) {
        const mappings = {
            oauthTokenInput: payload.token_v2 || '',
            oauthUserIdInput: payload.user_id || '',
            oauthSpaceIdInput: payload.space_id || '',
            oauthEmailInput: payload.user_email || '',
            oauthRedirectUriInput: payload.redirect_uri || window.location.origin,
        };

        Object.entries(mappings).forEach(([id, value]) => {
            const input = document.getElementById(id);
            if (input && !input.value.trim() && value) {
                input.value = value;
            }
        });
    },

    consumeOAuthCallbackParams() {
        const callback = this.getOAuthCallbackParams();
        const hasUsefulData = Boolean(callback.detected);
        if (!hasUsefulData || callback.consumed) {
            return;
        }

        this.fillOAuthFinalizeForm({
            ...callback,
            redirect_uri: window.location.origin,
        });
        this.setAdminNotice('已从本地 URL 检测到 OAuth callback 参数。请检查后点击“完成 OAuth 导入”。');
        callback.consumed = true;

        const cleanUrl = `${window.location.origin}${window.location.pathname}`;
        window.history.replaceState({}, document.title, cleanUrl);
    },

    extractCallbackUrl(rawValue) {
        const text = String(rawValue || '').trim();
        if (!text) {
            return '';
        }

        const directUrlMatch = text.match(/https?:\/\/[^\s'"<>]+/i);
        if (directUrlMatch) {
            return directUrlMatch[0];
        }

        if (text.startsWith('/') || text.startsWith('?') || text.startsWith('#')) {
            return `${window.location.origin}${text.startsWith('/') ? text : `${window.location.pathname}${text}`}`;
        }

        const callbackPathMatch = text.match(/(?:\/|^)(?:callback|oauth|auth)[^\s'"<>]*/i);
        if (callbackPathMatch) {
            const path = callbackPathMatch[0].startsWith('/')
                ? callbackPathMatch[0]
                : `/${callbackPathMatch[0]}`;
            return `${window.location.origin}${path}`;
        }

        return '';
    },

    parseManualCallbackUrl() {
        const input = document.getElementById('oauthCallbackUrlInput');
        const rawValue = input ? input.value.trim() : '';
        if (!rawValue) {
            this.setAdminNotice('请先粘贴完整的 callback URL。');
            return false;
        }

        const extractedUrl = this.extractCallbackUrl(rawValue);
        if (!extractedUrl) {
            this.setAdminNotice('在粘贴的内容中未找到有效的 callback URL。');
            return false;
        }

        this._oauthCallbackCache = null;
        const parsed = this.parseOAuthCallbackUrl(extractedUrl);
        if (!parsed.detected) {
            this.setAdminNotice('粘贴的 URL 中未找到支持的 OAuth callback 参数。');
            return false;
        }

        this.fillOAuthFinalizeForm({
            ...parsed,
            redirect_uri: window.location.origin,
        });
        this.setAdminNotice('已解析 callback URL。请检查字段后点击“完成 OAuth 导入”。');
        return true;
    },

    renderOAuthStartSummary(payload = {}) {
        const summary = document.getElementById('oauthStartSummary');
        const output = document.getElementById('oauthStartUrlOutput');
        const bridge = document.getElementById('oauthCallbackBridgeOutput');
        if (!summary) {
            return;
        }
        if (!payload || !payload.authorization_url) {
            summary.innerHTML = '';
            if (output) {
                output.value = '';
            }
            if (bridge) {
                bridge.value = '';
            }
            return;
        }
        summary.innerHTML = `
            <span class="admin-mini-pill"><strong>状态</strong><span>${payload.state || 'generated'}</span></span>
            <span class="admin-mini-pill"><strong>redirect URI</strong><span>${payload.redirect_uri || window.location.origin}</span></span>
        `;
        if (output) {
            output.value = payload.authorization_url;
        }
        if (bridge) {
            bridge.value = payload.callback_bridge_url || '';
        }
    },

    async startOAuthFlow() {
        const redirectUri = document.getElementById('oauthRedirectUriInput').value.trim() || window.location.origin;
        try {
            const result = await window.NotionAI.API.Admin.startOAuth({
                redirect_uri: redirectUri,
                provider: '网页会话',
            });
            this.renderOAuthStartSummary(result);
            if (result.authorization_url) {
                window.open(result.authorization_url, '_blank', 'noopener,noreferrer');
            }
            this.setAdminNotice('已生成 OAuth 启动链接和 callback bridge URL。如果上游 callback 无法直接访问 localhost，请在远端环境使用该桥接地址。');
        } catch (error) {
            this.setAdminNotice(error.message || '准备 OAuth 启动参数失败。');
        }
    },

    async loadOAuthRefreshStatus() {
        try {
            const result = await window.NotionAI.API.Admin.getOAuthRefreshStatus();
            this.setAdminNotice(result.message || result.status || 'OAuth 刷新状态已加载。');
        } catch (error) {
            this.setAdminNotice(error.message || '加载 OAuth 刷新状态失败。');
        }
    },

    async loadWorkspaceCreateStatus() {
        try {
            const result = await window.NotionAI.API.Admin.getWorkspaceCreateStatus();
            const hasTemplate = !!(result.request_template && result.request_template.operation);
            this.setAdminNotice(`${result.message || result.status || '工作区创建状态已加载。'}${hasTemplate ? ' 请求模板已就绪。' : ''}`);
        } catch (error) {
            this.setAdminNotice(error.message || '加载工作区创建状态失败。');
        }
    },

    async loadOAuthRefreshDiagnostics() {
        try {
            const result = await window.NotionAI.API.Admin.getOAuthRefreshDiagnostics();
            const summary = result.summary || {};
            this.renderRefreshDiagnostics(result);
            this.setAdminNotice(`刷新诊断已更新：可直接刷新 ${summary.refresh_ready ?? 0}，需手动重新授权 ${summary.manual_reauthorize ?? 0}，已过期 ${summary.expired ?? 0}。`);
        } catch (error) {
            this.setAdminNotice(error.message || '加载 OAuth 刷新诊断失败。');
        }
    },

    async loadWorkspaceDiagnostics() {
        try {
            const result = await window.NotionAI.API.Admin.getWorkspaceDiagnostics();
            const summary = result.summary || {};
            this.renderWorkspaceDiagnostics(result);
            this.setAdminNotice(`工作区诊断已更新：已就绪 ${summary.ready ?? 0}，缺失 ${summary.missing ?? summary.缺失 ?? 0}，待处理 ${summary.pending ?? 0}，未实现 ${summary.unimplemented ?? 0}。`);
        } catch (error) {
            this.setAdminNotice(error.message || '加载工作区诊断失败。');
        }
    },

    renderRefreshDiagnostics(result = {}) {
        const panel = document.getElementById('oauthRefreshDiagnosticsPanel');
        const output = document.getElementById('requestTemplateOutput');
        if (!panel) {
            return;
        }
        const accounts = Array.isArray(result.accounts) ? result.accounts : [];
        if (!accounts.length) {
            panel.innerHTML = '';
            return;
        }
        panel.innerHTML = `
            <div class="text-xs font-medium text-gray-700 dark:text-gray-200">刷新诊断</div>
            ${accounts.slice(0, 5).map((item) => `
                <div class="rounded-xl border border-black/10 dark:border-white/10 px-3 py-2 text-xs bg-black/[0.02] dark:bg-white/[0.03]">
                    <div><strong>${item.user_email || item.user_id || item.account_id}</strong></div>
                    <div>就绪状态：${item.readiness}</div>
                    <div>刷新令牌：${item.has_refresh_token ? '有' : '无'}</div>
                    <div>最近动作：${item.last_refresh_action || '无'}</div>
                    <div>错误：${item.last_refresh_error || '无'}</div>
                </div>
            `).join('')}
        `;
        if (output) {
            output.value = JSON.stringify(result, null, 2);
        }
    },

    renderWorkspaceDiagnostics(result = {}) {
        const panel = document.getElementById('workspaceDiagnosticsPanel');
        const output = document.getElementById('requestTemplateOutput');
        if (!panel) {
            return;
        }
        const accounts = Array.isArray(result.accounts) ? result.accounts : [];
        if (!accounts.length) {
            panel.innerHTML = '';
            return;
        }
        panel.innerHTML = `
            <div class="text-xs font-medium text-gray-700 dark:text-gray-200">工作区诊断</div>
            ${accounts.slice(0, 5).map((item) => `
                <div class="rounded-xl border border-black/10 dark:border-white/10 px-3 py-2 text-xs bg-black/[0.02] dark:bg-white/[0.03]">
                    <div><strong>${item.user_email || item.user_id || item.account_id}</strong></div>
                    <div>状态：${item.workspace_state}</div>
                    <div>数量：${item.workspace_count}</div>
                    <div>最近动作：${item.last_workspace_action || '无'}</div>
                    <div>错误：${item.last_workspace_error || '无'}</div>
                </div>
            `).join('')}
        `;
        if (output) {
            output.value = JSON.stringify(result, null, 2);
        }
    },

    async loadRequestTemplates() {
        try {
            const result = await window.NotionAI.API.Admin.getRequestTemplates();
            const output = document.getElementById('requestTemplateOutput');
            if (output) {
                output.value = JSON.stringify(result, null, 2);
            }
            const mode = String(result.response_mode || 'template_preview').trim().toLowerCase() || 'template_preview';
            this.setAdminNotice(`已加载通用请求模板，当前模式：${mode}。`);
        } catch (error) {
            this.setAdminNotice(error.message || '加载请求模板失败。');
        }
    },

    async copyRequestTemplateOutput() {
        const output = document.getElementById('requestTemplateOutput');
        if (!output || !output.value.trim()) {
            this.setAdminNotice('没有可复制的 JSON 模板。');
            return;
        }
        try {
            await navigator.clipboard.writeText(output.value);
            this.setAdminNotice('已复制 JSON 模板。');
        } catch (error) {
            this.setAdminNotice('复制 JSON 模板失败。');
        }
    },

    async loadAdminReport() {
        try {
            const result = await window.NotionAI.API.Admin.getAdminReport({
                action_account: this.getActionHistoryFilters().account,
            });
            const output = document.getElementById('requestTemplateOutput');
            if (output) {
                output.value = JSON.stringify(result, null, 2);
            }
            this.setAdminNotice('完整后台报告已加载。');
        } catch (error) {
            this.setAdminNotice(error.message || '加载后台报告失败。');
        }
    },

    async loadAdminSnapshot() {
        try {
            const result = await window.NotionAI.API.Admin.getAdminSnapshot({
                action_account: this.getActionHistoryFilters().account,
            });
            const output = document.getElementById('requestTemplateOutput');
            if (output) {
                output.value = JSON.stringify(result, null, 2);
            }
            this.setAdminNotice(`快照已更新：账号 ${result.summary?.accounts ?? 0} 个，告警 ${result.summary?.alerts ?? 0} 条，操作 ${result.summary?.operations ?? 0} 条。`);
        } catch (error) {
            this.setAdminNotice(error.message || '加载后台快照失败。');
        }
    },

    async autoFinalizeOAuthIfPossible() {
        const callback = this.getOAuthCallbackParams();
        if (!callback.detected || callback.autoFinalized) {
            return;
        }

        const adminSessionToken = window.NotionAI.Core.State.get('adminSessionToken');
        if (!adminSessionToken || !callback.token_v2 || !callback.user_id) {
            return;
        }

        callback.autoFinalized = true;
        this.fillOAuthFinalizeForm({
            ...callback,
            redirect_uri: window.location.origin,
        });

        try {
            await window.NotionAI.API.Admin.finalizeOAuth({
                token_v2: callback.token_v2,
                user_id: callback.user_id,
                space_id: callback.space_id,
                user_email: callback.user_email,
                redirect_uri: window.location.origin,
                access_token: callback.access_token,
                refresh_token: callback.refresh_token,
                expires_at: callback.expires_at ? Number(callback.expires_at) : undefined,
                state: callback.state,
            });
            await this.refreshAdminPanel('OAuth callback 已自动导入账号池。');
        } catch (error) {
            callback.autoFinalized = false;
            this.setAdminNotice(error.message || '自动完成 OAuth callback 失败。');
        }
    },

    open(moduleName = 'access') {
        const baseUrl = window.NotionAI.Core.State.get('baseUrl');
        const apiKey = window.NotionAI.Core.State.get('apiKey');
        const adminUsername = window.NotionAI.Core.State.get('adminUsername') || 'admin';

        document.getElementById('baseUrlInput').value = baseUrl;
        document.getElementById('apiKeyInput').value = apiKey;
        document.getElementById('adminUsernameInput').value = adminUsername;
        document.getElementById('adminPasswordInput').value = '';
        this.applySecretVisibility();
        this.applyRuntimeAdvancedVisibility();
        const redirectInput = document.getElementById('oauthRedirectUriInput');
        if (redirectInput && !redirectInput.value.trim()) {
            redirectInput.value = window.location.origin;
        }
        this.loadRuntimeConfigIntoForm();
        this.consumeOAuthCallbackParams();
        this.autoFinalizeOAuthIfPossible();
        if (typeof window.NotionAI.Core.App?.setActiveModule === 'function') {
            window.NotionAI.Core.App.setActiveModule(moduleName || 'access');
        }
    },

    close() {
        if (typeof window.NotionAI.Core.App?.setActiveModule === 'function') {
            window.NotionAI.Core.App.setActiveModule(window.NotionAI.Core.App.getDefaultModule());
        }
    },

    async updateAdminCredentialsOnly() {
        const adminUsername = document.getElementById('adminUsernameInput').value.trim() || 'admin';
        const adminPassword = document.getElementById('adminPasswordInput').value.trim();
        const adminNewUsername = (document.getElementById('adminNewUsernameInput')?.value || '').trim();
        const adminNewPassword = (document.getElementById('adminNewPasswordInput')?.value || '').trim();

        if (!adminPassword) {
            this.setAdminNotice('更新凭证前，请先输入当前 admin 密码。');
            return;
        }

        try {
            await window.NotionAI.API.Admin.login(adminUsername, adminPassword);
            const shouldChangeCredentials = Boolean(adminNewUsername || adminNewPassword);
            if (!shouldChangeCredentials) {
                this.setAdminNotice('请输入新的 admin 用户名、新密码，或同时输入两者。');
                return;
            }
            const changeResult = await window.NotionAI.API.Admin.changePassword({
                current_password: adminPassword,
                new_password: adminNewPassword || undefined,
                new_username: adminNewUsername || adminUsername,
            });
            const changedUsername = String(changeResult.username || adminNewUsername || adminUsername).trim() || adminUsername;
            document.getElementById('adminUsernameInput').value = changedUsername;
            document.getElementById('adminPasswordInput').value = '';
            const newUsernameInput = document.getElementById('adminNewUsernameInput');
            if (newUsernameInput) {
                newUsernameInput.value = '';
            }
            const newPasswordInput = document.getElementById('adminNewPasswordInput');
            if (newPasswordInput) {
                newPasswordInput.value = '';
            }
            await this.refreshAdminPanel('当前浏览器会话中的 admin 凭证已更新。');
        } catch (error) {
            this.setAdminNotice(error.message || '更新后台凭证失败。');
        }
    },

    async save() {
        const baseUrl = document.getElementById('baseUrlInput').value.trim().replace(/\/$/, '');
        const apiKey = document.getElementById('apiKeyInput').value.trim();
        const adminUsername = document.getElementById('adminUsernameInput').value.trim() || 'admin';
        const adminPassword = document.getElementById('adminPasswordInput').value.trim();
        const adminNewUsername = (document.getElementById('adminNewUsernameInput')?.value || '').trim();
        const adminNewPassword = (document.getElementById('adminNewPasswordInput')?.value || '').trim();

        window.NotionAI.Core.State.set('baseUrl', baseUrl);
        window.NotionAI.Core.State.set('apiKey', apiKey);
        window.NotionAI.Core.State.set('adminUsername', adminUsername);
        window.NotionAI.Core.State.set('adminPassword', '');

        localStorage.setItem('claude_base_url', baseUrl);
        window.NotionAI.Core.State.persistApiKey(apiKey);

        if (adminPassword) {
            try {
                await window.NotionAI.API.Admin.login(adminUsername, adminPassword);
                const shouldChangeCredentials = Boolean(adminNewUsername || adminNewPassword);
                if (shouldChangeCredentials) {
                    const changeResult = await window.NotionAI.API.Admin.changePassword({
                        current_password: adminPassword,
                        new_password: adminNewPassword || undefined,
                        new_username: adminNewUsername || adminUsername,
                    });
                    const changedUsername = String(changeResult.username || adminNewUsername || adminUsername).trim() || adminUsername;
                    document.getElementById('adminUsernameInput').value = changedUsername;
                    document.getElementById('adminPasswordInput').value = '';
                    const newUsernameInput = document.getElementById('adminNewUsernameInput');
                    if (newUsernameInput) {
                        newUsernameInput.value = '';
                    }
                    const newPasswordInput = document.getElementById('adminNewPasswordInput');
                    if (newPasswordInput) {
                        newPasswordInput.value = '';
                    }
                }
                await this.saveRuntimeConfigFromForm(true);
                await this.refreshAdminPanel(shouldChangeCredentials ? '当前浏览器会话的后台凭证已更新。' : '当前浏览器会话的 admin session 已就绪。');
            } catch (error) {
                this.setAdminNotice(error.message || '后台登录失败。');
                return;
            }
        }

        this.close();
        window.NotionAI.API.Models.loadModels();
    },

    applySecretVisibility() {
        const type = this._runtimeSecretsVisible ? 'text' : 'password';
        ['runtimeServerApiKeyInput', 'runtimeSiliconflowApiKeyInput', 'runtimeAutoRegisterMailApiKeyInput', 'runtimeRefreshClientSecretInput', 'adminPasswordInput', 'adminNewPasswordInput'].forEach((id) => {
            const input = document.getElementById(id);
            if (input) {
                input.type = type;
            }
        });
        const toggle = document.getElementById('runtimeToggleSecretsBtn');
        if (toggle) {
            toggle.textContent = this._runtimeSecretsVisible ? '隐藏密钥' : '显示密钥';
        }
    },

    applyRuntimeAdvancedVisibility() {
        const container = document.getElementById('runtimeAdvancedFields');
        const toggle = document.getElementById('runtimeAdvancedToggleBtn');
        if (container) {
            container.classList.toggle('hidden', !this._runtimeAdvancedVisible);
        }
        if (toggle) {
            toggle.textContent = this._runtimeAdvancedVisible ? '收起高级设置' : '展开高级设置';
        }
    },

    toggleRuntimeAdvanced() {
        this._runtimeAdvancedVisible = !this._runtimeAdvancedVisible;
        this.applyRuntimeAdvancedVisibility();
    },

    toggleRuntimeSecrets() {
        this._runtimeSecretsVisible = !this._runtimeSecretsVisible;
        this.applySecretVisibility();
    },

    setAdminNotice(message) {
        const notice = document.getElementById('adminPanelNotice');
        if (notice) {
            notice.textContent = message;
        }
    },

    signOutAdminSession() {
        window.NotionAI.API.Admin.logout();
        const passwordInput = document.getElementById('adminPasswordInput');
        if (passwordInput) {
            passwordInput.value = '';
        }
        this.renderAdminAccessStatus({});
        this.renderAdminSessionSummary({});
        this.applyAdminConsoleAccessState({});
        this.setAdminNotice('当前浏览器会话中的 admin session 已清除。');
    },

    renderBulkActionResult(result) {
        const panel = document.getElementById('adminBulkResultPanel');
        if (!panel) {
            return;
        }
        if (!result) {
            panel.innerHTML = '';
            return;
        }
        const failedItems = (result.results || []).filter((item) => item && item.ok === false);
        panel.innerHTML = `
            <div><strong>批量动作：</strong>${result.action || '未知'} | 成功 ${result.success_count ?? 0} | 失败 ${result.failed_count ?? 0}</div>
            ${failedItems.map((item) => `<div>失败 ${item.account_id || item.account || '未知'}：${item.error || item.reason || '未知错误'}</div>`).join('')}
        `;
    },

    renderOperationLogs(data = {}) {
        const panel = document.getElementById('adminOperationHistory');
        if (!panel) {
            return;
        }
        const logs = Array.isArray(data.logs) ? data.logs : [];
        if (!logs.length) {
            panel.innerHTML = '';
            return;
        }
        panel.innerHTML = logs.slice(-5).reverse().map((item) => {
            const date = item.timestamp ? new Date(item.timestamp * 1000).toLocaleString() : '未知时间';
            const exportMode = item.action === 'accounts_export' && item.export_mode
                ? ` | 模式 ${item.export_mode}`
                : '';
            return `<div><strong>${item.action || 'action'}</strong>${exportMode} | 成功 ${item.success_count ?? 0} | 失败 ${item.failed_count ?? 0} | ${date}</div>`;
        }).join('');
    },

    renderProbeLogs(snapshot = {}) {
        const panel = document.getElementById('adminProbeHistory');
        if (!panel) {
            return;
        }
        const logs = Array.isArray(snapshot.recent_probes) ? snapshot.recent_probes : [];
        if (!logs.length) {
            panel.innerHTML = '';
            return;
        }
        panel.innerHTML = logs.slice(-5).reverse().map((item) => {
            const date = item.timestamp ? new Date(item.timestamp * 1000).toLocaleString() : '未知时间';
            const summary = item.payload?.summary || {};
            const mode = summary.mode || '';
            const reason = summary.reason || '';
            const statusCode = summary.status_code ?? 'n/a';
            const contentType = summary.content_type || 'content-type-n/a';
            const format = summary.response_format || 'format-n/a';
            const recognizedFields = summary.recognized_fields && typeof summary.recognized_fields === 'object'
                ? Object.entries(summary.recognized_fields).slice(0, 4).map(([key, value]) => `${key}=${typeof value === 'string' ? value : JSON.stringify(value)}`).join(' | ')
                : '';
            const parseError = summary.parse_error || '';
            return `<div><strong>${item.action || 'probe'}</strong> | ${mode || 'mode-n/a'} | 状态码 ${statusCode} | ${format} | ${contentType} | ${date}<br>${reason || ''}${recognizedFields ? `<br>${recognizedFields}` : ''}${parseError ? `<br>解析错误：${parseError}` : ''}</div>`;
        }).join('');
    },

    renderUsageSummary(data = {}) {
        const panel = document.getElementById('adminUsageSummary');
        const byModelPanel = document.getElementById('adminUsageByModel');
        const byAccountPanel = document.getElementById('adminUsageByAccount');
        if (!panel) {
            return;
        }
        const summary = data.summary || {};
        const items = [
            ['请求数', summary.request_count ?? 0, '当前范围内记录的请求总数'],
            ['输入 Token', summary.prompt_tokens ?? 0, '输入 token 估算值'],
            ['输出 Token', summary.completion_tokens ?? 0, '输出 token 估算值'],
            ['总 Token', summary.total_tokens ?? 0, '输入与输出 token 总量'],
            ['平均每次请求', summary.avg_total_tokens ?? 0, '每次请求的平均 token 数'],
            ['模型数', summary.distinct_models ?? 0, '当前范围内涉及的模型数量'],
            ['账号数', summary.distinct_accounts ?? 0, '当前范围内涉及的账号数量'],
            ['流式请求', summary.stream_request_count ?? 0, '以流式方式返回的请求数'],
        ];
        panel.innerHTML = items.map(([label, value, hint]) => `
            <div class="usage-summary-card">
                <div class="usage-summary-card-label">${label}</div>
                <div class="usage-summary-card-value">${value}</div>
                <div class="usage-summary-card-hint">${hint}</div>
            </div>
        `).join('');
        if (byModelPanel) {
            const byModel = Array.isArray(summary.by_model) ? summary.by_model : [];
            byModelPanel.innerHTML = byModel.length
                ? byModel.map((item) => `
                    <div class="usage-breakdown-row">
                        <div class="usage-breakdown-main">
                            <div class="usage-breakdown-name">${item.model || '未知模型'}</div>
                            <div class="usage-breakdown-meta">${item.request_count ?? 0} 次请求</div>
                        </div>
                        <div class="usage-breakdown-value">${item.total_tokens ?? 0}</div>
                    </div>
                `).join('')
                : '<div class="usage-empty-state">当前筛选条件下没有匹配到模型用量。</div>';
        }
        if (byAccountPanel) {
            const byAccount = Array.isArray(summary.by_account) ? summary.by_account : [];
            byAccountPanel.innerHTML = byAccount.length
                ? byAccount.map((item) => `
                    <div class="usage-breakdown-row">
                        <div class="usage-breakdown-main">
                            <div class="usage-breakdown-name">${item.account_id || '未知账号'}</div>
                            <div class="usage-breakdown-meta">${item.request_count ?? 0} 次请求</div>
                        </div>
                        <div class="usage-breakdown-value">${item.total_tokens ?? 0}</div>
                    </div>
                `).join('')
                : '<div class="usage-empty-state">当前筛选条件下没有匹配到账号用量。</div>';
        }
    },

    renderUsageEvents(data = {}) {
        const panel = document.getElementById('adminUsageEvents');
        const meta = document.getElementById('adminUsageEventsMeta');
        if (!panel) {
            return;
        }
        const events = Array.isArray(data.events) ? data.events : [];
        if (meta) {
            const total = Number(data.total || 0);
            const offset = Number(data.offset || 0);
            const shownUntil = events.length ? offset + events.length : 0;
            meta.textContent = total ? `显示第 ${offset + 1}-${shownUntil} 条，共 ${total} 条事件${data.has_more ? ' · 还有更多' : ''}` : '当前筛选条件下没有匹配到用量事件。';
        }
        if (!events.length) {
            panel.innerHTML = '<div class="usage-empty-state">暂未记录到用量事件。</div>';
            return;
        }
        panel.innerHTML = events.map((item) => {
            const created = this.formatTimestamp(item.created_at);
            return `
                <div class="usage-event-row">
                    <div class="usage-event-main">
                        <div class="usage-event-title">${item.model || '未知模型'} · ${item.request_type || 'chat.completions'}</div>
                        <div class="usage-event-meta">总量 ${item.total_tokens ?? 0} · 输入 ${item.prompt_tokens ?? 0} · 输出 ${item.completion_tokens ?? 0}</div>
                        <div class="usage-event-meta">账号 ${item.account_id || '未知'} · 会话 ${item.conversation_id || '无'} · ${created}</div>
                    </div>
                </div>
            `;
        }).join('');
    },

    renderActionHistory(snapshot = {}) {
        const panel = document.getElementById('adminActionHistory');
        if (!panel) {
            return;
        }
        const logs = Array.isArray(snapshot.recent_actions) ? snapshot.recent_actions : [];
        const filters = this.getActionHistoryFilters();
        const filteredLogs = logs.filter((item) => {
            const summary = item.payload?.summary || {};
            const accountNeedle = String(filters.account || '').trim().toLowerCase();
            if (accountNeedle) {
                const accountCandidates = [
                    item.payload?.account_id,
                    summary.account_id,
                    item.payload?.user_id,
                    summary.user_id,
                    item.payload?.user_email,
                    summary.user_email,
                ]
                    .map((value) => String(value || '').trim().toLowerCase())
                    .filter(Boolean);
                if (!accountCandidates.includes(accountNeedle)) {
                    return false;
                }
            }
            if (filters.type && String(item.action || '') !== filters.type) {
                return false;
            }
            if (filters.status === 'failed' && summary.ok !== false) {
                return false;
            }
            if (filters.status === 'success' && summary.ok === false) {
                return false;
            }
            if (filters.failureCategory && String(summary.failure_category || '') !== filters.failureCategory) {
                return false;
            }
            if (filters.reauthOnly && !summary.reauthorize_required) {
                return false;
            }
            return true;
        });
        if (!filteredLogs.length) {
            panel.innerHTML = '<div>当前筛选条件下没有匹配到操作历史。</div>';
            return;
        }
        panel.innerHTML = filteredLogs.slice(-5).reverse().map((item) => {
            const date = item.timestamp ? new Date(item.timestamp * 1000).toLocaleString() : '未知时间';
            const summary = item.payload?.summary || {};
            const reason = summary.reason || '';
            const accountId = item.payload?.account_id || summary.account_id || '未知-account';
            const spaceId = summary.space_id || 'space-n/a';
            const failureCategory = summary.failure_category || '无';
            const statusCode = summary.status_code ?? 'n/a';
            const reauthorizeRequired = summary.reauthorize_required ? 'reauth' : 'no-reauth';
            const suggestion = this.getActionSuggestion(summary);
            const actionKey = `${item.timestamp || 0}-${item.action || 'action'}-${accountId}`;
            const expanded = this._expandedActionHistoryKeys[actionKey] === true;
            const payloadJson = JSON.stringify(item.payload || {}, null, 2);
            const summaryJson = JSON.stringify(item.payload?.summary || {}, null, 2);
            const resultJson = JSON.stringify(item.payload?.result || {}, null, 2);
            const recognizedFields = summary.recognized_fields && typeof summary.recognized_fields === 'object'
                ? Object.entries(summary.recognized_fields).slice(0, 4).map(([key, value]) => `${key}=${typeof value === 'string' ? value : JSON.stringify(value)}`).join(' | ')
                : '';
            const okLabel = summary.ok === false ? 'failed' : 'ok';
            const remediation = summary.remediation_message || '';
            return `<div class="rounded-lg border border-black/10 dark:border-white/10 px-3 py-2 bg-black/[0.02] dark:bg-white/[0.03]"><div><strong>${item.action || 'action'}</strong> | ${okLabel} | ${failureCategory} | ${statusCode} | ${reauthorizeRequired} | ${accountId} | ${spaceId} | ${date}<br>${summary.action || 'action-n/a'}${reason ? ` · ${reason}` : ''}${suggestion ? `<br>建议：${suggestion}` : ''}${remediation ? `<br>指引：${remediation}` : ''}${recognizedFields ? `<br>${recognizedFields}` : ''}</div><div class="mt-2 flex flex-wrap gap-2"><button type="button" class="admin-action-btn admin-action-history-toggle" data-action-key="${actionKey}">${expanded ? '隐藏 JSON' : '查看 JSON'}</button><button type="button" class="admin-action-btn admin-action-history-copy" data-copy-kind="payload" data-action-json="${this.escapeHtmlAttribute(payloadJson)}">复制 payload</button><button type="button" class="admin-action-btn admin-action-history-copy" data-copy-kind="summary" data-action-json="${this.escapeHtmlAttribute(summaryJson)}">复制 summary</button><button type="button" class="admin-action-btn admin-action-history-copy" data-copy-kind="result" data-action-json="${this.escapeHtmlAttribute(resultJson)}">复制 result</button></div>${expanded ? `<pre class="mt-2 whitespace-pre-wrap break-all text-[11px] text-gray-600 dark:text-gray-300 bg-gray-50 dark:bg-[#1f1f1f] rounded-lg p-2 overflow-auto">${this.escapeHtml(payloadJson)}</pre>` : ''}</div>`;
        }).join('');
        panel.querySelectorAll('.admin-action-history-toggle').forEach((button) => {
            button.addEventListener('click', (event) => {
                const actionKey = event.currentTarget.dataset.actionKey || '';
                if (!actionKey) {
                    return;
                }
                this._expandedActionHistoryKeys[actionKey] = !this._expandedActionHistoryKeys[actionKey];
                this.renderActionHistory(this._lastAdminSnapshot || {});
            });
        });
        panel.querySelectorAll('.admin-action-history-copy').forEach((button) => {
            button.addEventListener('click', async (event) => {
                const payload = event.currentTarget.dataset.actionJson || '';
                const copyKind = event.currentTarget.dataset.copyKind || 'payload';
                if (!payload) {
                    this.setAdminNotice('没有可复制的操作历史 JSON。');
                    return;
                }
                try {
                    await navigator.clipboard.writeText(payload);
                    this.setAdminNotice(`已复制操作历史 ${copyKind} JSON。`);
                } catch (error) {
                    this.setAdminNotice(`复制操作历史 ${copyKind} JSON 失败。`);
                }
            });
        });
    },

    escapeHtml(value) {
        return String(value || '')
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#39;');
    },

    escapeHtmlAttribute(value) {
        return this.escapeHtml(value).replaceAll('\n', '&#10;');
    },

    getActionSuggestion(summary = {}) {
        if (summary.suggested_action && summary.suggested_action !== 'none') {
            const suggestedAction = String(summary.suggested_action).replaceAll('_', ' ');
            const translationMap = {
                'reauthorize account': '重新授权账号',
                'retry later': '稍后重试',
                'retry and inspect upstream': '重试并检查上游',
                'check runtime config': '检查运行时配置',
                'inspect action details': '检查动作详情',
            };
            return translationMap[suggestedAction] || suggestedAction;
        }
        const category = String(summary.failure_category || '').trim().toLowerCase();
        if (!category || category === 'success') {
            return 'none';
        }
        if (summary.reauthorize_required || category === 'unauthorized' || category === 'forbidden') {
            return '重新授权账号';
        }
        if (category === 'rate_limited') {
            return '稍后重试';
        }
        if (category === 'timeout' || category === 'server_error' || category === 'network_error') {
            return '重试并检查上游';
        }
        if (category === 'client_error' || category === 'not_found') {
            return '检查运行时配置';
        }
        return '检查动作详情';
    },

    renderPagination(pagination = {}) {
        const summary = document.getElementById('adminPaginationSummary');
        const prevBtn = document.getElementById('adminPrevPageBtn');
        const nextBtn = document.getElementById('adminNextPageBtn');
        const page = Number(pagination.page || 1);
        const totalPages = Number(pagination.total_pages || 1);
        const total = Number(pagination.total || 0);
        if (summary) {
            summary.textContent = `第 ${page} / ${Math.max(1, totalPages)} 页 · ${total} 个账号`;
        }
        if (prevBtn) {
            prevBtn.disabled = page <= 1;
        }
        if (nextBtn) {
            nextBtn.disabled = page >= totalPages;
        }
    },

    formatTimestamp(timestamp) {
        const value = Number(timestamp || 0);
        if (!value) {
            return '从未';
        }
        const date = new Date(value * 1000);
        if (Number.isNaN(date.getTime())) {
            return '无效时间';
        }
        return date.toLocaleString();
    },

    renderAdminAccounts(data) {
        const panel = document.getElementById('adminAccountsPanel');
        if (!panel) {
            return;
        }

        const accounts = Array.isArray(data.accounts) ? data.accounts : [];
        const viewMode = String(data.view_mode || 'safe').trim().toLowerCase() || 'safe';
        const viewBanner = `<div class="px-4 py-3 text-xs border-b border-black/10 dark:border-white/10 text-gray-600 dark:text-gray-300">视图模式：<strong>${viewMode}</strong>${viewMode === 'safe' ? ' · 列表视图中的 secrets 已遮罩。' : ' · 当前响应中可见原始账号数据。'}</div>`;
        if (!accounts.length) {
            panel.innerHTML = `${viewBanner}<div class="px-4 py-4 text-sm text-gray-500 dark:text-gray-400">未加载到账号。</div>`;
            return;
        }

        const severityOrder = {
            oauth_expired: 0,
            invalid: 1,
            needs_refresh: 2,
            workspace_creation_pending: 3,
            no_workspace: 4,
            cooling: 5,
            active: 6,
            disabled: 7,
            unknown: 8,
        };
        const sortedAccounts = [...accounts].sort((a, b) => {
            const aState = a.status?.effective_state || 'unknown';
            const bState = b.status?.effective_state || 'unknown';
            const aRank = severityOrder[aState] ?? 99;
            const bRank = severityOrder[bState] ?? 99;
            if (aRank !== bRank) {
                return aRank - bRank;
            }
            return String(a.user_email || a.user_id || '').localeCompare(String(b.user_email || b.user_id || ''));
        });

        panel.innerHTML = viewBanner + sortedAccounts.map((account) => {
            const status = account.status || {};
            const workspace = account.workspace || {};
            const oauth = account.oauth || {};
            const label = account.user_email || account.user_id || account.id || '未知';
            const toggleLabel = account.enabled === false ? '启用' : '停用';
            const tags = Array.isArray(account.tags) ? account.tags : [];
            const planCategory = account.plan_category || status.plan_category || '未知';
            const badgeItems = [];
            if (status.last_probe_failure_category) {
                badgeItems.push({ state: 'probe_failures', label: `probe:${status.last_probe_failure_category}` });
            }
            if (status.last_refresh_failure_category && status.last_refresh_failure_category !== 'success') {
                badgeItems.push({ state: 'invalid', label: `refresh:${status.last_refresh_failure_category}` });
            }
            if (status.last_workspace_failure_category && status.last_workspace_failure_category !== 'success') {
                badgeItems.push({ state: 'workspace_creation_pending', label: `workspace:${status.last_workspace_failure_category}` });
            }
            if (status.workspace_hydration_operator_classification) {
                badgeItems.push({ state: 'workspace_creation_pending', label: `hydration:${status.workspace_hydration_operator_classification}` });
            }
            if (status.workspace_hydration_pending) {
                badgeItems.push({ state: 'workspace_creation_pending', label: 'hydration:pending' });
            }
            if (status.workspace_expand_error) {
                badgeItems.push({ state: 'cooling', label: `expand:${status.workspace_expand_status_code || 'warn'}` });
            }
            if (!badgeItems.length) {
                badgeItems.push({ state: status.effective_state || 'unknown', label: status.effective_state || '未知' });
            }
            return `
                <div class="admin-account-row" data-account-id="${account.id || ''}">
                    <div class="admin-account-main">
                        <div class="text-sm font-medium text-gray-800 dark:text-gray-100">${label}</div>
                        <div class="text-xs text-gray-500 dark:text-gray-400">${account.plan_type || '未知'} · ${planCategory} · ${workspace.subscription_tier || 'no tier'} · ${workspace.workspace_count || 0} workspace(s)</div>
                        <div class="flex flex-wrap gap-2 mt-1">
                            ${(tags.length ? tags : [account.source || 'manual']).map((tag) => `<span class="admin-mini-pill">${tag}</span>`).join('')}
                        </div>
                        <div class="text-xs text-gray-500 dark:text-gray-400 mt-2">${account.notes || ''}</div>
                    </div>
                    <div class="admin-kv text-xs text-gray-600 dark:text-gray-300">
                        ${badgeItems.map((badge) => `<span class="admin-badge" data-state="${badge.state}">${badge.label}</span>`).join('')}
                        <span>${status.usable ? '可用' : '需关注'}</span>
                        <span>工作区：${status.workspace_state || workspace.state || '缺失'}</span>
                    </div>
                    <div class="admin-kv text-xs text-gray-600 dark:text-gray-300">
                        <strong>刷新 / 鉴权</strong>
                        <span>OAuth：${oauth.expired ? '已过期' : '有效'}</span>
                        <span>需要刷新：${oauth.needs_refresh ? '是' : '否'}</span>
                        <span>过期时间：${oauth.expires_at ? this.formatTimestamp(oauth.expires_at) : '未知'}</span>
                        <span>最近刷新：${this.formatTimestamp(status.last_refresh_at)}</span>
                        <span>刷新动作：${status.last_refresh_action || '无'}</span>
                        <span>刷新失败：${status.last_refresh_failure_category || '无'}</span>
                        <span>重新授权：${status.reauthorize_required ? '需要' : '暂不需要'}</span>
                    </div>
                    <div class="admin-kv text-xs text-gray-600 dark:text-gray-300">
                        <strong>健康状态</strong>
                        <span>保活失败：${status.keepalive_failures || 0}</span>
                        <span>最近成功：${this.formatTimestamp(status.last_success_at)}</span>
                        <span>错误：${status.last_error || '无'}</span>
                        <span>工作区检查次数：${status.workspace_poll_count || 0}</span>
                        <span>工作区动作：${status.last_workspace_action || '无'}</span>
                        <span>工作区检查时间：${this.formatTimestamp(status.last_workspace_check_at)}</span>
                        <span>补全挂起：${status.workspace_hydration_pending ? '是' : '否'}</span>
                        <span>补全重试时间：${this.formatTimestamp(status.workspace_hydration_retry_after)}</span>
                        <span>补全退避：${status.workspace_hydration_backoff_seconds || 0} 秒</span>
                        <span>补全策略：${status.workspace_hydration_retry_policy || '无'}</span>
                        <span>补全分类：${status.workspace_hydration_operator_classification || '无'}</span>
                        <span>刷新恢复：${status.workspace_hydration_refresh_recovery_attempted ? (status.workspace_hydration_refresh_recovery_ok ? '已恢复' : '失败') : '未尝试'}</span>
                        <span>补全指引：${status.workspace_hydration_guidance || '无'}</span>
                        <span>补全下一步：${status.workspace_hydration_next_step || '无'}</span>
                        <span>最近探测：${status.last_probe_action || '无'}</span>
                        <span>探测成功：${status.last_probe_ok ? '是' : '否'}</span>
                        <span>探测状态码：${status.last_probe_status_code ?? 'n/a'}</span>
                        <span>探测分类：${status.last_probe_failure_category || '无'}</span>
                        <span>探测类型：${status.last_probe_content_type || '未知'}</span>
                        <span>探测格式：${status.last_probe_response_format || '未知'}</span>
                        <span>探测限流：${status.probe_rate_limited ? '是' : '否'}</span>
                        <span>探测原因：${status.last_probe_reason || '无'}</span>
                        <span>探测字段：${status.last_probe_recognized_fields ? JSON.stringify(status.last_probe_recognized_fields) : '{}'}</span>
                        <span>探测解析错误：${status.last_probe_parse_error || '无'}</span>
                        <span>刷新动作：${status.last_refresh_action || '无'}</span>
                        <span>工作区动作：${status.last_workspace_action || '无'}</span>
                        <span>工作区失败：${status.last_workspace_failure_category || '无'}</span>
                        <span>扩展警告：${status.workspace_expand_status_code ? `${status.workspace_expand_status_code}` : (status.workspace_expand_error ? '是' : '无')}</span>
                        <span>扩展错误：${status.workspace_expand_error || '无'}</span>
                    </div>
                    <div class="text-xs text-gray-600 dark:text-gray-300 flex flex-col gap-2">
                        <div class="admin-inline-actions">
                            <button type="button" class="admin-action-btn admin-edit-btn">编辑</button>
                            <button type="button" class="admin-action-btn admin-template-btn">模板</button>
                            <button type="button" class="admin-action-btn admin-refresh-probe-btn">刷新探测</button>
                            <button type="button" class="admin-action-btn admin-workspace-probe-btn">工作区探测</button>
                            <button type="button" class="admin-action-btn admin-probe-btn">探测</button>
                            <button type="button" class="admin-action-btn admin-refresh-btn">刷新</button>
                            <button type="button" class="admin-action-btn admin-sync-btn">同步工作区</button>
                            <button type="button" class="admin-action-btn admin-hydration-retry-btn">补全重试</button>
                            <button type="button" class="admin-action-btn admin-create-ws-btn">创建工作区</button>
                        </div>
                        <button type="button" class="admin-action-btn admin-toggle-btn" data-enabled="${account.enabled !== false ? 'true' : 'false'}">${toggleLabel}</button>
                        <button type="button" class="admin-action-btn admin-delete-btn">删除</button>
                        <button type="button" class="admin-action-btn admin-tags-btn">保存标签</button>
                        <input type="text" class="admin-tag-input w-full bg-gray-50 dark:bg-[#1f1f1f] border border-gray-200 dark:border-white/10 rounded-lg px-2 py-1 text-xs outline-none" value="${tags.join(', ')}" placeholder="标签，多个用逗号分隔">
                        <button type="button" class="admin-action-btn admin-note-btn">保存备注</button>
                        <textarea class="admin-note-input w-full bg-gray-50 dark:bg-[#1f1f1f] border border-gray-200 dark:border-white/10 rounded-lg px-2 py-1 text-xs outline-none" rows="2" placeholder="备注">${account.notes || ''}</textarea>
                    </div>
                </div>
            `;
        }).join('');

        panel.querySelectorAll('.admin-toggle-btn').forEach((button) => {
            button.addEventListener('click', async (event) => {
                const row = event.currentTarget.closest('.admin-account-row');
                const accountId = row?.dataset.accountId;
                const enabled = event.currentTarget.dataset.enabled === 'true';
                if (!accountId) {
                    return;
                }
                try {
                    await window.NotionAI.API.Admin.toggleAccount(accountId, !enabled);
                    await this.refreshAdminPanel('账号状态已更新。');
                } catch (error) {
                    this.setAdminNotice(error.message || '切换账号状态失败。');
                }
            });
        });

        panel.querySelectorAll('.admin-edit-btn').forEach((button) => {
            button.addEventListener('click', async (event) => {
                const row = event.currentTarget.closest('.admin-account-row');
                const accountId = row?.dataset.accountId;
                if (!accountId) {
                    return;
                }
                const data = await window.NotionAI.API.Admin.getAccount(accountId);
                const account = data.account;
                if (account) {
                    this.fillAccountForm(account);
                }
            });
        });

        panel.querySelectorAll('.admin-template-btn').forEach((button) => {
            button.addEventListener('click', async (event) => {
                const row = event.currentTarget.closest('.admin-account-row');
                const accountId = row?.dataset.accountId;
                if (!accountId) {
                    return;
                }
                try {
                    const result = await window.NotionAI.API.Admin.getAccountRequestTemplates(accountId);
                    const output = document.getElementById('requestTemplateOutput');
                    if (output) {
                        output.value = JSON.stringify(result, null, 2);
                    }
                    const mode = String(result.response_mode || 'template_preview').trim().toLowerCase() || 'template_preview';
                    this.setAdminNotice(`已加载账号级请求模板，当前模式：${mode}。`);
                } catch (error) {
                    this.setAdminNotice(error.message || '加载账号请求模板失败。');
                }
            });
        });

        panel.querySelectorAll('.admin-refresh-probe-btn').forEach((button) => {
            button.addEventListener('click', async (event) => {
                const row = event.currentTarget.closest('.admin-account-row');
                const accountId = row?.dataset.accountId;
                if (!accountId) {
                    return;
                }
                try {
                    const result = await window.NotionAI.API.Admin.runRefreshProbe(accountId);
                    const output = document.getElementById('requestTemplateOutput');
                    if (output) {
                        output.value = JSON.stringify(result, null, 2);
                    }
                    this.setAdminNotice('已以 dry-run 模式执行刷新探测。');
                } catch (error) {
                    this.setAdminNotice(error.message || '执行刷新探测失败。');
                }
            });
        });

        panel.querySelectorAll('.admin-workspace-probe-btn').forEach((button) => {
            button.addEventListener('click', async (event) => {
                const row = event.currentTarget.closest('.admin-account-row');
                const accountId = row?.dataset.accountId;
                if (!accountId) {
                    return;
                }
                try {
                    const result = await window.NotionAI.API.Admin.runWorkspaceProbe(accountId);
                    const output = document.getElementById('requestTemplateOutput');
                    if (output) {
                        output.value = JSON.stringify(result, null, 2);
                    }
                    this.setAdminNotice('已以 dry-run 模式执行工作区探测。');
                } catch (error) {
                    this.setAdminNotice(error.message || '执行工作区探测失败。');
                }
            });
        });

        panel.querySelectorAll('.admin-probe-btn').forEach((button) => {
            button.addEventListener('click', async (event) => {
                const row = event.currentTarget.closest('.admin-account-row');
                const accountId = row?.dataset.accountId;
                if (!accountId) return;
                try {
                    await window.NotionAI.API.Admin.runAccountAction(accountId, 'probe');
                    await this.refreshAdminPanel('单账号探测已完成。');
                } catch (error) {
                    this.setAdminNotice(error.message || '探测账号失败。');
                }
            });
        });

        panel.querySelectorAll('.admin-refresh-btn').forEach((button) => {
            button.addEventListener('click', async (event) => {
                const row = event.currentTarget.closest('.admin-account-row');
                const accountId = row?.dataset.accountId;
                if (!accountId) return;
                try {
                    await window.NotionAI.API.Admin.runAccountAction(accountId, 'refresh');
                    await this.refreshAdminPanel('单账号刷新尝试已完成。');
                } catch (error) {
                    this.setAdminNotice(error.message || '刷新账号失败。');
                }
            });
        });

        panel.querySelectorAll('.admin-sync-btn').forEach((button) => {
            button.addEventListener('click', async (event) => {
                const row = event.currentTarget.closest('.admin-account-row');
                const accountId = row?.dataset.accountId;
                if (!accountId) return;
                try {
                    await window.NotionAI.API.Admin.runAccountAction(accountId, 'workspaces/sync');
                    await this.refreshAdminPanel('单账号工作区同步已完成。');
                } catch (error) {
                    this.setAdminNotice(error.message || '同步工作区失败。');
                }
            });
        });

        panel.querySelectorAll('.admin-hydration-retry-btn').forEach((button) => {
            button.addEventListener('click', async (event) => {
                const row = event.currentTarget.closest('.admin-account-row');
                const accountId = row?.dataset.accountId;
                if (!accountId) return;
                try {
                    const result = await window.NotionAI.API.Admin.retryRegisterHydration(accountId);
                    const output = document.getElementById('requestTemplateOutput');
                    if (output) {
                        output.value = JSON.stringify(result, null, 2);
                    }
                    await this.refreshAdminPanel('注册补全重试已完成。');
                } catch (error) {
                    this.setAdminNotice(error.message || '重试注册补全失败。');
                }
            });
        });

        panel.querySelectorAll('.admin-create-ws-btn').forEach((button) => {
            button.addEventListener('click', async (event) => {
                const row = event.currentTarget.closest('.admin-account-row');
                const accountId = row?.dataset.accountId;
                if (!accountId) return;
                try {
                    await window.NotionAI.API.Admin.runAccountAction(accountId, 'workspaces/create');
                    await this.refreshAdminPanel('已触发单账号工作区创建。');
                } catch (error) {
                    this.setAdminNotice(error.message || '创建工作区失败。');
                }
            });
        });

        panel.querySelectorAll('.admin-note-btn').forEach((button) => {
            button.addEventListener('click', async (event) => {
                const row = event.currentTarget.closest('.admin-account-row');
                const accountId = row?.dataset.accountId;
                const input = row?.querySelector('.admin-note-input');
                if (!accountId || !input) {
                    return;
                }
                try {
                    await window.NotionAI.API.Admin.patchAccount(accountId, { notes: input.value.trim() });
                    await this.refreshAdminPanel('账号备注已更新。');
                } catch (error) {
                    this.setAdminNotice(error.message || '保存备注失败。');
                }
            });
        });

        panel.querySelectorAll('.admin-tags-btn').forEach((button) => {
            button.addEventListener('click', async (event) => {
                const row = event.currentTarget.closest('.admin-account-row');
                const accountId = row?.dataset.accountId;
                const input = row?.querySelector('.admin-tag-input');
                if (!accountId || !input) {
                    return;
                }
                const tags = input.value
                    .split(',')
                    .map((item) => item.trim())
                    .filter(Boolean);
                try {
                    await window.NotionAI.API.Admin.patchAccount(accountId, { tags });
                    await this.refreshAdminPanel('账号标签已更新。');
                } catch (error) {
                    this.setAdminNotice(error.message || '保存标签失败。');
                }
            });
        });

        panel.querySelectorAll('.admin-delete-btn').forEach((button) => {
            button.addEventListener('click', async (event) => {
                const row = event.currentTarget.closest('.admin-account-row');
                const accountId = row?.dataset.accountId;
                if (!accountId) {
                    return;
                }
                const confirmed = window.confirm('要从运行时配置中删除这个账号吗？');
                if (!confirmed) {
                    return;
                }
                try {
                    await window.NotionAI.API.Admin.deleteAccount(accountId);
                    await this.refreshAdminPanel('账号已删除。');
                } catch (error) {
                    this.setAdminNotice(error.message || '删除账号失败。');
                }
            });
        });
    },

    renderAdminSummary(summary = {}) {
        document.getElementById('adminStatTotal').textContent = summary.total ?? '-';
        document.getElementById('adminStatUsable').textContent = summary.usable ?? '-';
        document.getElementById('adminStatNeedsRefresh').textContent = summary.needs_refresh ?? '-';
        document.getElementById('adminStatWorkspace').textContent = summary.workspace_creation_pending ?? summary.no_workspace ?? '-';

        const quick = document.getElementById('adminQuickSummary');
        if (!quick) {
            return;
        }
        const items = [
            ['已停用', summary.disabled ?? 0],
            ['无效', summary.invalid ?? 0],
            ['冷却中', summary.cooling ?? 0],
            ['OAuth 已过期', summary.oauth_expired ?? 0],
            ['缺少工作区', summary.no_workspace ?? 0],
            ['补全挂起', summary.workspace_creation_pending ?? 0],
            ['补全到期', summary.workspace_hydration_due ?? 0],
            ['探测失败', summary.probe_failures ?? 0],
        ];
        quick.innerHTML = items.map(([label, value]) => `<span class="admin-mini-pill"><strong>${value}</strong><span>${label}</span></span>`).join('');
    },

    renderAdminAlerts(alerts = {}) {
        const summary = document.getElementById('adminAlertSummary');
        const details = document.getElementById('adminAlertDetails');
        if (!summary) {
            return;
        }
        const counts = alerts.summary || {};
        const items = [
            ['无效', counts.invalid ?? 0],
            ['已过期', counts.oauth_expired ?? 0],
            ['待刷新', counts.needs_refresh ?? 0],
            ['缺少工作区', counts.no_workspace ?? 0],
            ['工作区待处理', counts.workspace_creation_pending ?? 0],
            ['补全到期', counts.workspace_hydration_due ?? 0],
            ['探测失败', counts.probe_failures ?? 0],
            ['动作失败', counts.action_failures ?? 0],
            ['动作需重授权', counts.action_reauth_required ?? 0],
            ['动作 429', counts.action_rate_limited ?? 0],
            ['扩展警告', counts.workspace_expand_warnings ?? 0],
        ];
        summary.innerHTML = items.map(([label, value]) => `<span class="admin-mini-pill"><strong>${value}</strong><span>${label}</span></span>`).join('');

        if (!details) {
            return;
        }

        const grouped = alerts.items || {};
        const sections = [
            ['invalid', '无效'],
            ['oauth_expired', 'OAuth 已过期'],
            ['needs_refresh', '需要刷新'],
            ['no_workspace', '缺少工作区'],
            ['workspace_creation_pending', '工作区待处理'],
            ['probe_failures', '探测失败'],
            ['action_failures', '动作失败'],
            ['action_reauth_required', '动作需重授权'],
            ['action_rate_limited', '动作被限流'],
            ['workspace_expand_warnings', '工作区扩展警告'],
        ];

        details.innerHTML = sections.map(([key, label]) => {
            const rows = Array.isArray(grouped[key]) ? grouped[key] : [];
            if (!rows.length) {
                return '';
            }
            const preview = rows.slice(0, 3).map((item) => {
                const title = item.user_email || item.user_id || item.account_id || '未知';
                const reason = item.failure_category || item.probe_failure_category || item.workspace_expand_error || item.last_error || item.last_refresh_error || item.workspace_state || '无详情';
                return `<div class="text-[11px] text-gray-600 dark:text-gray-300">${title} - ${reason}</div>`;
            }).join('');
            const actionHint = key === 'action_failures' || key === 'action_reauth_required' || key === 'action_rate_limited'
                ? '<div class="mt-2 text-[11px] text-gray-500 dark:text-gray-400">会直接应用操作历史筛选。</div>'
                : '';
            return `
                <div class="rounded-xl border border-black/10 dark:border-white/10 px-3 py-2 bg-black/[0.02] dark:bg-white/[0.03]">
                    <div class="flex items-center justify-between gap-2">
                        <div class="text-xs font-medium">${label}</div>
                        <button type="button" class="admin-action-btn admin-alert-filter-btn" data-alert-type="${key}">查看筛选结果</button>
                    </div>
                    <div class="mt-2 space-y-1">${preview}</div>
                    ${actionHint}
                </div>
            `;
        }).join('');

        details.querySelectorAll('.admin-alert-filter-btn').forEach((button) => {
            button.addEventListener('click', (event) => {
                const type = event.currentTarget.dataset.alertType;
                this.applyAlertFilter(type);
            });
        });
    },

    async refreshAdminPanel(message) {
        try {
            const usageFilters = this.getUsageFilters();
            const data = await window.NotionAI.API.Admin.loadSafeAccounts(this.getAdminFilters());
            const runtimeConfig = await window.NotionAI.API.Admin.loadConfig();
            const alerts = await window.NotionAI.API.Admin.loadAlerts();
            const operations = await window.NotionAI.API.Admin.loadOperationLogs();
            const snapshot = await window.NotionAI.API.Admin.getAdminSnapshot({
                action_account: this.getActionHistoryFilters().account,
            });
            const usageSummary = await window.NotionAI.API.Admin.getUsageSummary(usageFilters);
            const usageEvents = await window.NotionAI.API.Admin.getUsageEvents(usageFilters);
            this._lastAdminSnapshot = snapshot || {};
            this.renderAdminSummary(data.summary || {});
            this.renderAdminAuthSource(runtimeConfig.admin_auth || {});
            this.renderAdminAccessStatus(runtimeConfig.admin_auth || {});
            this.renderAdminSessionSummary(runtimeConfig.admin_auth || {});
            this.renderAdminAlerts(alerts || {});
            this.applyAdminConsoleAccessState(runtimeConfig.admin_auth || {});
            this.renderOperationLogs(operations || {});
            this.renderUsageSummary(usageSummary || {});
            this.renderUsageEvents(usageEvents || {});
            this._lastAdminAlertsMeta = alerts || {};
            this._lastAdminOperationsMeta = operations || {};
            this.renderActionHistory(snapshot || {});
            this.renderProbeLogs(snapshot || {});
            this.renderPagination(data.pagination || {});
            this.renderAdminAccounts(data);
            const viewMode = String(data.view_mode || 'safe').trim().toLowerCase() || 'safe';
            const alertsMode = String((alerts || {}).response_mode || 'safe_summary').trim().toLowerCase() || 'safe_summary';
            const operationsMode = String((operations || {}).response_mode || 'audit_log').trim().toLowerCase() || 'audit_log';
            this.setAdminNotice(message || `后台面板已同步，当前视图：${viewMode}。告警模式：${alertsMode}。操作模式：${operationsMode}。`);
        } catch (error) {
            this._lastAdminSnapshot = {};
            this.renderAdminAccounts({ accounts: [] });
            this.setAdminNotice(error.message || '加载后台账号失败。');
        }
    },

    async runAdminAction(endpoint, successMessage) {
        try {
            await window.NotionAI.API.Admin.trigger(endpoint);
            this.renderBulkActionResult(null);
            await this.refreshAdminPanel(successMessage);
        } catch (error) {
            this.setAdminNotice(error.message || '后台动作执行失败。');
        }
    },

    applyRuntimeSettingsToForm(settings = {}) {
        this._runtimeSecretPresence = {
            api_key: Boolean(settings.has_api_key || settings.api_key),
            siliconflow_api_key: Boolean(settings.has_siliconflow_api_key || settings.siliconflow_api_key),
            auto_register_mail_api_key: Boolean(settings.has_auto_register_mail_api_key || settings.auto_register_mail_api_key),
            refresh_client_secret: Boolean(settings.has_refresh_client_secret || settings.refresh_client_secret),
        };
        const mappings = {
            runtimeAppModeInput: settings.app_mode || 'standard',
            runtimeProbeIntervalInput: settings.account_probe_interval_seconds ?? 300,
            runtimeAllowedOriginsInput: Array.isArray(settings.allowed_origins) ? settings.allowed_origins.join(', ') : '*',
            runtimeServerApiKeyInput: '',
            runtimeSiliconflowApiKeyInput: '',
            runtimeProxyInput: settings.upstream_proxy || '',
            runtimeProxyModeInput: settings.upstream_proxy_mode || 'direct',
            runtimeHttpProxyInput: settings.upstream_http_proxy || '',
            runtimeHttpsProxyInput: settings.upstream_https_proxy || '',
            runtimeSocks5ProxyInput: settings.upstream_socks5_proxy || '',
            runtimeWarpProxyInput: settings.upstream_warp_proxy || '',
            runtimeAutoRegisterMailBaseUrlInput: settings.auto_register_mail_base_url || '',
            runtimeAutoRegisterMailApiKeyInput: '',
            runtimeAutoRegisterDomainInput: settings.auto_register_domain || '',
            runtimeAutoRegisterMailProviderInput: settings.auto_register_mail_provider || 'freemail',
            runtimeAutoRegisterIntervalInput: settings.auto_register_interval_seconds ?? 1800,
            runtimeAutoRegisterMinSpacingInput: settings.auto_register_min_spacing_seconds ?? 900,
            runtimeAutoRegisterBusyCooldownInput: settings.auto_register_busy_cooldown_seconds ?? 1200,
            runtimeAutoRegisterBatchSizeInput: settings.auto_register_batch_size ?? 1,
            runtimeTemplateSpaceInput: settings.workspace_creation_template_space_id || '',
            runtimeRefreshExecutionModeInput: settings.refresh_execution_mode || 'manual',
            runtimeRefreshRequestUrlInput: settings.refresh_request_url || '',
            runtimeRefreshClientIdInput: settings.refresh_client_id || '',
            runtimeRefreshClientSecretInput: '',
            runtimeWorkspaceExecutionModeInput: settings.workspace_execution_mode || 'manual',
            runtimeWorkspaceRequestUrlInput: settings.workspace_request_url || '',
        };

        Object.entries(mappings).forEach(([id, value]) => {
            const element = document.getElementById(id);
            if (element) {
                element.value = value;
            }
        });

        const secretPlaceholders = {
            runtimeServerApiKeyInput: this._runtimeSecretPresence?.api_key,
            runtimeSiliconflowApiKeyInput: this._runtimeSecretPresence?.siliconflow_api_key,
            runtimeAutoRegisterMailApiKeyInput: this._runtimeSecretPresence?.auto_register_mail_api_key,
            runtimeRefreshClientSecretInput: this._runtimeSecretPresence?.refresh_client_secret,
        };
        Object.entries(secretPlaceholders).forEach(([id, hasValue]) => {
            const element = document.getElementById(id);
            if (element && hasValue) {
                element.placeholder = '已存储在 server，留空则保持不变。';
            }
        });

        const checkboxMappings = {
            runtimeAutoCreateWorkspaceInput: Boolean(settings.auto_create_workspace),
            runtimeAutoSelectWorkspaceInput: settings.auto_select_workspace !== false,
            runtimeWorkspaceDryRunInput: settings.workspace_create_dry_run !== false,
            runtimeAllowRealProbeRequestsInput: Boolean(settings.allow_real_probe_requests),
            runtimeWarpEnabledInput: Boolean(settings.upstream_warp_enabled),
            runtimeAutoRegisterEnabledInput: Boolean(settings.auto_register_enabled),
            runtimeAutoRegisterIdleOnlyInput: settings.auto_register_idle_only !== false,
            runtimeAutoRegisterHeadlessInput: Boolean(settings.auto_register_headless),
            runtimeAutoRegisterUseApiInput: settings.auto_register_use_api !== false,
            runtimeChatEnabledInput: Boolean(settings.chat_enabled),
        };

        Object.entries(checkboxMappings).forEach(([id, checked]) => {
            const element = document.getElementById(id);
            if (element) {
                element.checked = checked;
            }
        });

        this.renderRuntimeConfigSummary(settings);
        this.applyRuntimeAdvancedVisibility();
    },

    renderAdminAuthSource(auth = {}) {
        const summary = document.getElementById('adminAuthSourceSummary');
        if (!summary) {
            return;
        }
        const sourceLabel = String(auth.auth_source_label || '').trim() || '未知';
        const items = [
            ['来源', sourceLabel],
            ['已配置', auth.configured ? '是' : '否'],
            ['用户名', auth.username || 'admin'],
        ];
        summary.innerHTML = items
            .map(([label, value]) => `<span class="admin-mini-pill"><strong>${label}</strong><span>${value}</span></span>`)
            .join('');
    },

    renderAdminAccessStatus(auth = {}) {
        const banner = document.getElementById('adminAccessStatusBanner');
        const title = document.getElementById('adminAccessStatusTitle');
        const detail = document.getElementById('adminAccessStatusDetail');
        const meta = document.getElementById('adminAccessStatusMeta');
        const signOutBtn = document.getElementById('adminSignOutBtn');
        if (!banner || !title || !detail || !meta || !signOutBtn) {
            return;
        }
        const sessionToken = window.NotionAI.Core.State.get('adminSessionToken') || '';
        const sessionExpiresAt = Number(window.NotionAI.Core.State.get('adminSessionExpiresAt') || 0);
        const adminUsername = window.NotionAI.Core.State.get('adminUsername') || auth.username || 'admin';
        let state = 'signed_out';
        let titleText = '需要登录';
        let detailText = '请使用当前后台用户名和密码，在本浏览器会话中建立 admin session 并打开运维控制台。';
        if (sessionToken) {
            state = 'ready';
            titleText = '后台已就绪';
            detailText = `admin session 已就绪${sessionExpiresAt ? `，有效期至 ${this.formatTimestamp(sessionExpiresAt)}` : ''}。现在可以查看运行时、账号、诊断和用量信息。`;
        }
        const metaItems = [
            ['后台用户', adminUsername],
            ['凭证来源', String(auth.auth_source_label || '').trim() || '未知'],
            ['凭证更新时间', this.formatTimestamp(auth.updated_at)],
        ];
        banner.dataset.state = state;
        title.textContent = titleText;
        detail.textContent = detailText;
        meta.innerHTML = metaItems
            .map(([label, value]) => `<span class="admin-access-meta-item"><strong>${label}</strong><span>${value}</span></span>`)
            .join('');
        signOutBtn.disabled = !sessionToken;
    },

    buildAdminConsoleEmptyState() {
        const sessionToken = window.NotionAI.Core.State.get('adminSessionToken') || '';
        if (sessionToken) {
            return '';
        }
        return '<div class="admin-empty-state" data-state="signed_out"><div class="admin-empty-state-title">登录后解锁运维控制台</div><div class="admin-empty-state-copy">总览、用量、账号和诊断模块会保持待命状态，直到当前浏览器使用后台账号完成登录。</div></div>';
    },

    applyAdminConsoleAccessState() {
        const emptyState = this.buildAdminConsoleEmptyState();
        const sessionToken = window.NotionAI.Core.State.get('adminSessionToken') || '';
        const accessState = sessionToken ? 'ready' : 'signed_out';
        const sectionStatuses = {
            runtimeSectionStatus: {
                ready: '运行时控制已就绪',
                signed_out: '登录后台后解锁运行时控制',
            },
            overviewSectionStatus: {
                ready: '总览已就绪',
                signed_out: '登录后台后解锁总览',
            },
            usageSectionStatus: {
                ready: '用量已就绪',
                signed_out: '登录后台后解锁用量查询',
            },
            accountsSectionStatus: {
                ready: '账号管理已就绪',
                signed_out: '登录后台后解锁账号管理',
            },
            diagnosticsSectionStatus: {
                ready: '诊断已就绪',
                signed_out: '登录后台后解锁诊断',
            },
        };
        Object.entries(sectionStatuses).forEach(([id, labels]) => {
            const element = document.getElementById(id);
            if (!element) {
                return;
            }
            const text = labels[accessState] || labels.signed_out;
            element.dataset.state = accessState;
            element.innerHTML = `<span class="admin-section-status-dot"></span><strong>${accessState === 'ready' ? '已就绪' : '已锁定'}</strong><span>${text}</span>`;
        });
        const targets = [
            'adminAlertDetails',
            'adminActionHistory',
            'adminProbeHistory',
            'adminOperationHistory',
            'adminAccountsPanel',
            'adminUsageByModel',
            'adminUsageByAccount',
            'adminUsageEvents',
        ];
        const lockableButtons = [
            'runtimeLoadBtn',
            'runtimeSaveBtn',
            'runtimeCheckProxyBtn',
            'runtimeTriggerAutoRegisterBtn',
            'runtimeRefreshAutoRegisterBtn',
            'adminRefreshBtn',
            'adminApplyFiltersBtn',
            'adminClearFiltersBtn',
            'adminBulkProbeBtn',
            'adminBulkRefreshBtn',
            'adminBulkRefreshProbeBtn',
            'adminBulkEnableBtn',
            'adminBulkDisableBtn',
            'adminBulkSyncWorkspaceBtn',
            'adminBulkHydrationRetryBtn',
            'adminBulkCreateWorkspaceBtn',
            'adminBulkWorkspaceProbeBtn',
            'adminQuickProbeFailuresBtn',
            'adminQuickInvalidBtn',
            'adminQuickRefreshBtn',
            'adminQuickWorkspaceBtn',
            'adminQuickPendingHydrationBtn',
            'adminQuickHydrationDueBtn',
            'adminQuickEducationBtn',
            'adminQuickUsableBtn',
            'adminPrevPageBtn',
            'adminNextPageBtn',
            'adminUsageApplyBtn',
            'adminUsageClearBtn',
            'adminProbeBtn',
            'adminRefreshTokensBtn',
            'adminWorkspaceSyncBtn',
            'adminWorkspaceCreateBtn',
            'adminExportAccountsBtn',
            'adminAddAccountBtn',
            'adminClearAccountFormBtn',
            'adminImportAccountsBtn',
            'adminReplaceAccountsBtn',
            'oauthStartBtn',
            'oauthRefreshStatusBtn',
            'workspaceCreateStatusBtn',
            'oauthRefreshDiagnosticsBtn',
            'workspaceDiagnosticsBtn',
            'requestTemplatesBtn',
            'adminReportBtn',
            'adminSnapshotBtn',
            'copyRequestTemplateBtn',
            'showGenericTemplatesBtn',
        ];
        const shouldDisableActions = accessState !== 'ready';
        lockableButtons.forEach((id) => {
            const button = document.getElementById(id);
            if (button) {
                button.disabled = shouldDisableActions;
                button.title = shouldDisableActions ? '登录后才能使用此操作。' : '';
            }
        });
        if (!emptyState) {
            const usageMeta = document.getElementById('adminUsageEventsMeta');
            if (usageMeta && usageMeta.dataset.locked === 'true') {
                usageMeta.textContent = '';
                delete usageMeta.dataset.locked;
            }
            return;
        }
        targets.forEach((id) => {
            const element = document.getElementById(id);
            if (element) {
                element.innerHTML = emptyState;
            }
        });
        const usageMeta = document.getElementById('adminUsageEventsMeta');
        if (usageMeta) {
            usageMeta.textContent = '登录后台后，才可查看用量查询。';
            usageMeta.dataset.locked = 'true';
        }
    },

    renderAdminSessionSummary(auth = {}) {
        const summary = document.getElementById('adminAccessSessionSummary');
        if (!summary) {
            return;
        }
        const sessionToken = window.NotionAI.Core.State.get('adminSessionToken') || '';
        const sessionExpiresAt = Number(window.NotionAI.Core.State.get('adminSessionExpiresAt') || 0);
        const items = [
            ['session', sessionToken ? '活跃' : '缺失'],
            ['session 过期时间', sessionToken ? this.formatTimestamp(sessionExpiresAt) : '未登录'],
            ['面板访问', sessionToken ? '已就绪' : '需要登录'],
            ['更新时间', this.formatTimestamp(auth.updated_at)],
        ];
        summary.innerHTML = items
            .map(([label, value]) => `<span class="admin-mini-pill"><strong>${label}</strong><span>${value}</span></span>`)
            .join('');
    },

    renderRuntimeConfigSummary(settings = {}) {
        const summary = document.getElementById('runtimeConfigSummary');
        if (!summary) {
            return;
        }
        const items = [
            ['模式', settings.app_mode || 'standard'],
            ['探测间隔', `${settings.account_probe_interval_seconds ?? 300} 秒`],
            ['自动建工作区', settings.auto_create_workspace ? '开启' : '关闭'],
            ['干跑模式', settings.workspace_create_dry_run !== false ? '开启' : '关闭'],
            ['代理模式', settings.upstream_proxy_mode || 'direct'],
            ['Warp', settings.upstream_warp_enabled ? '开启' : '关闭'],
            ['自动注册', settings.auto_register_enabled ? '开启' : '关闭'],
            ['自动注册空闲限制', settings.auto_register_idle_only !== false ? '仅空闲时' : '始终可触发'],
            ['服务端密钥', (settings.has_api_key || settings.api_key) ? '已设置' : '为空'],
            ['刷新模式', settings.refresh_execution_mode || 'manual'],
            ['刷新地址', settings.refresh_request_url ? '已设置' : '为空'],
            ['工作区模式', settings.workspace_execution_mode || 'manual'],
            ['真实探测', settings.allow_real_probe_requests ? '已放开' : '已阻止'],
        ];
        summary.innerHTML = items
            .map(([label, value]) => `<span class="admin-mini-pill"><strong>${label}</strong><span>${value}</span></span>`)
            .join('');
    },

    renderRuntimeProxyHealth(proxyHealth = {}) {
        const container = document.getElementById('runtimeProxyHealthSummary');
        if (!container) {
            return;
        }
        this._lastRuntimeProxyHealth = proxyHealth || {};
        const items = [
            ['代理活跃', proxyHealth.active ? '是' : '否'],
            ['模式', proxyHealth.mode || 'direct'],
            ['状态', proxyHealth.operator_state || 'direct'],
            ['Warp', proxyHealth.warp_enabled ? '启用' : '停用'],
            ['Warp 代理', proxyHealth.warp_configured ? '已设置' : '为空'],
            ['SOCKS5', proxyHealth.socks5_configured ? '已设置' : '为空'],
            ['HTTP', proxyHealth.http_configured ? '已设置' : '为空'],
            ['HTTPS', proxyHealth.https_configured ? '已设置' : '为空'],
            ['可达目标数', String(proxyHealth.reachable_target_count ?? 0)],
        ];
        container.innerHTML = items
            .map(([label, value]) => `<span class="admin-mini-pill"><strong>${label}</strong><span>${value}</span></span>`)
            .join('');
        const hint = document.getElementById('runtimeConfigHint');
        if (hint && proxyHealth.hint) {
            hint.textContent = proxyHealth.hint;
        }
    },

    renderRuntimeProxyChecks(checks = {}) {
        const container = document.getElementById('runtimeProxyChecks');
        if (!container) {
            return;
        }
        const entries = Object.entries(checks || {});
        if (!entries.length) {
            container.innerHTML = '';
            return;
        }
        container.innerHTML = entries.map(([label, payload]) => {
            const state = payload?.configured
                ? (payload?.reachable ? '可达' : '不可达')
                : '未配置';
            const detail = payload?.host ? `${payload.host}:${payload.port || ''}` : '无';
            const error = payload?.error ? `（${payload.error}）` : '';
            return `<div><strong>${label}</strong>：${state} · ${detail}${error}</div>`;
        }).join('');
    },

    renderRegisterAutomationSummary(automation = {}) {
        const container = document.getElementById('runtimeRegisterAutomationSummary');
        if (!container) {
            return;
        }
        this._lastRegisterAutomationSummary = automation || {};
        const items = [
            ['自动注册活跃', automation.active ? '是' : '否'],
            ['允许执行', automation.eligible ? '是' : '否'],
            ['最近启动', this.formatTimestamp(automation.last_started_at)],
            ['最近结束', this.formatTimestamp(automation.last_finished_at)],
            ['最近任务', automation.last_task_id || '无'],
            ['任务状态', automation.latest_task_status || '无'],
            ['最近决策', automation.last_decision_reason || '未知'],
            ['当前门禁', automation.current_reason || automation.last_decision_reason || '未知'],
            ['阻塞原因', automation.gate_reason || automation.current_reason || '未知'],
            ['待补全数量', automation.pending_hydration_due ?? 0],
        ];
        container.innerHTML = items
            .map(([label, value]) => `<span class="admin-mini-pill"><strong>${label}</strong><span>${value}</span></span>`)
            .join('');
    },

    renderRegisterAutomationGuidance(guidance = {}) {
        const container = document.getElementById('runtimeRegisterAutomationGuidance');
        if (!container) {
            return;
        }
        const message = guidance?.message || '';
        const nextStep = guidance?.next_step || '';
        const severity = guidance?.severity || 'info';
        const focus = guidance?.operator_focus || '';
        const blockers = Array.isArray(guidance?.blockers) ? guidance.blockers.filter(Boolean) : [];
        const dueBreakdown = [];
        if ((guidance?.pending_hydration_due_reauthorize || 0) > 0) {
            dueBreakdown.push(`重授权 ${guidance.pending_hydration_due_reauthorize}`);
        }
        if ((guidance?.pending_hydration_due_transient || 0) > 0) {
            dueBreakdown.push(`传输 ${guidance.pending_hydration_due_transient}`);
        }
        if ((guidance?.pending_hydration_due_config || 0) > 0) {
            dueBreakdown.push(`配置 ${guidance.pending_hydration_due_config}`);
        }
        if ((guidance?.pending_hydration_due_unknown || 0) > 0) {
            dueBreakdown.push(`人工 ${guidance.pending_hydration_due_unknown}`);
        }
        if (!message) {
            container.textContent = '';
            return;
        }
        const blockerText = blockers.length ? ` 阻塞项：${blockers.join('、')}。` : '';
        const focusText = focus ? ` 当前关注：${focus}。` : '';
        const dueText = dueBreakdown.length ? ` 待处理拆分：${dueBreakdown.join('、')}。` : '';
        container.textContent = `【${severity}】${message}${focusText}${dueText}${blockerText}${nextStep ? ` 下一步：${nextStep}` : ''}`;
    },

    renderRuntimeOperationsPanel(panel = {}) {
        const container = document.getElementById('runtimeOperationsPanel');
        if (!container) {
            return;
        }
        this._lastRuntimeOperationsPanel = panel || {};
        const headline = panel?.headline || '';
        const recommendedAction = panel?.recommended_action || '';
        const operatorFocus = panel?.operator_focus || 'guarded';
        const proxyTargets = Array.isArray(panel?.reachable_proxy_targets) ? panel.reachable_proxy_targets : [];
        const details = [
            `关注点：${operatorFocus}`,
            `代理模式：${panel?.proxy_mode || 'direct'}`,
            `代理状态：${panel?.proxy_operator_state || 'direct'}`,
            `自动注册状态：${panel?.current_reason || '未知'}`,
            `门禁阻塞：${panel?.gate_reason || panel?.current_reason || '未知'}`,
            `最近任务：${panel?.latest_task_status || '无'}`,
            `待补全：${panel?.pending_hydration_due ?? 0}/${panel?.pending_hydration_total ?? 0}`,
        ];
        if ((panel?.pending_hydration_due_reauthorize || 0) > 0) {
            details.push(`待重授权：${panel.pending_hydration_due_reauthorize}`);
        }
        if ((panel?.pending_hydration_due_transient || 0) > 0) {
            details.push(`待传输恢复：${panel.pending_hydration_due_transient}`);
        }
        if ((panel?.pending_hydration_due_config || 0) > 0) {
            details.push(`待配置修复：${panel.pending_hydration_due_config}`);
        }
        if ((panel?.pending_hydration_due_unknown || 0) > 0) {
            details.push(`待人工处理：${panel.pending_hydration_due_unknown}`);
        }
        if ((panel?.spacing_remaining_seconds || 0) > 0) {
            details.push(`间隔剩余：${panel.spacing_remaining_seconds} 秒`);
        }
        if ((panel?.busy_cooldown_remaining_seconds || 0) > 0) {
            details.push(`冷却剩余：${panel.busy_cooldown_remaining_seconds} 秒`);
        }
        if ((panel?.next_eligible_at || 0) > 0) {
            details.push(`下次可执行：${this.formatTimestamp(panel.next_eligible_at)}`);
        }
        if (proxyTargets.length) {
            details.push(`可达代理：${proxyTargets.join('、')}`);
        }
        container.innerHTML = `
            <div><strong>当前运维：</strong>${headline || '暂未获取到运行时状态。'}</div>
            <div><strong>建议下一步：</strong>${recommendedAction || '建议先检查运行时健康状态，再决定是否触发自动化。'}</div>
            <div><strong>关键信号：</strong>${details.join(' · ')}</div>
        `;
    },

    renderRuntimeConfigPaths(storage = {}) {
        const container = document.getElementById('runtimeConfigPaths');
        if (!container) {
            return;
        }
        const runtimePath = storage.runtime_config_path || '-';
        const accountsPath = storage.accounts_path || '-';
        container.innerHTML = `
            <div><strong>运行时配置文件：</strong>${runtimePath}</div>
            <div><strong>账号文件：</strong>${accountsPath}</div>
        `;
    },

    async loadRuntimeConfigIntoForm() {
        try {
            const data = await window.NotionAI.API.Admin.loadConfig();
            this.applyRuntimeSettingsToForm(data.settings || {});
            this.renderAdminAuthSource(data.admin_auth || {});
            this.renderAdminAccessStatus(data.admin_auth || {});
            this.renderAdminSessionSummary(data.admin_auth || {});
            this.applyAdminConsoleAccessState(data.admin_auth || {});
            window.NotionAI.Core.State.set('chatEnabled', Boolean((data.settings || {}).chat_enabled));
            if (typeof window.NotionAI.Core.App?.syncShellFromState === 'function') {
                window.NotionAI.Core.App.syncShellFromState();
            }
            this.renderRuntimeProxyHealth(data.proxy_health || {});
            this.renderRegisterAutomationSummary(data.register_automation || {});
            this.renderRegisterAutomationGuidance(data.register_automation_guidance || {});
            this.renderRuntimeProxyChecks(data.proxy_health_checks || {});
            this.renderRuntimeOperationsPanel(data.runtime_operations_panel || {});
            this.renderRuntimeConfigPaths(data.storage || {});
            const redactionMode = String(data.redaction_mode || data.settings_view_mode || 'safe').trim().toLowerCase() || 'safe';
            this.setAdminNotice(`运行时配置已加载，当前模式：${redactionMode}。`);
        } catch (error) {
            this.setAdminNotice(error.message || '加载运行时配置失败。');
        }
    },

    async saveRuntimeConfigFromForm(silent = false) {
        const apiKeyValue = document.getElementById('runtimeServerApiKeyInput').value.trim();
        const siliconflowApiKeyValue = document.getElementById('runtimeSiliconflowApiKeyInput').value.trim();
        const autoRegisterMailApiKeyValue = document.getElementById('runtimeAutoRegisterMailApiKeyInput').value.trim();
        const refreshClientSecretValue = document.getElementById('runtimeRefreshClientSecretInput').value.trim();
        const payload = {
            app_mode: document.getElementById('runtimeAppModeInput').value || 'standard',
            api_key: apiKeyValue || (this._runtimeSecretPresence?.api_key ? undefined : ''),
            allowed_origins: document.getElementById('runtimeAllowedOriginsInput').value
                .split(',')
                .map((item) => item.trim())
                .filter(Boolean),
            siliconflow_api_key: siliconflowApiKeyValue || (this._runtimeSecretPresence?.siliconflow_api_key ? undefined : ''),
            upstream_proxy: document.getElementById('runtimeProxyInput').value.trim(),
            upstream_proxy_mode: document.getElementById('runtimeProxyModeInput').value || 'direct',
            upstream_http_proxy: document.getElementById('runtimeHttpProxyInput').value.trim(),
            upstream_https_proxy: document.getElementById('runtimeHttpsProxyInput').value.trim(),
            upstream_socks5_proxy: document.getElementById('runtimeSocks5ProxyInput').value.trim(),
            upstream_warp_enabled: document.getElementById('runtimeWarpEnabledInput').checked,
            upstream_warp_proxy: document.getElementById('runtimeWarpProxyInput').value.trim(),
            auto_register_enabled: document.getElementById('runtimeAutoRegisterEnabledInput').checked,
            auto_register_idle_only: document.getElementById('runtimeAutoRegisterIdleOnlyInput').checked,
            auto_register_interval_seconds: Number(document.getElementById('runtimeAutoRegisterIntervalInput').value || 1800),
            auto_register_min_spacing_seconds: Number(document.getElementById('runtimeAutoRegisterMinSpacingInput').value || 900),
            auto_register_busy_cooldown_seconds: Number(document.getElementById('runtimeAutoRegisterBusyCooldownInput').value || 1200),
            auto_register_batch_size: Number(document.getElementById('runtimeAutoRegisterBatchSizeInput').value || 1),
            auto_register_headless: document.getElementById('runtimeAutoRegisterHeadlessInput').checked,
            auto_register_use_api: document.getElementById('runtimeAutoRegisterUseApiInput').checked,
            auto_register_mail_provider: document.getElementById('runtimeAutoRegisterMailProviderInput').value || 'freemail',
            auto_register_mail_base_url: document.getElementById('runtimeAutoRegisterMailBaseUrlInput').value.trim(),
            auto_register_mail_api_key: autoRegisterMailApiKeyValue || (this._runtimeSecretPresence?.auto_register_mail_api_key ? undefined : ''),
            auto_register_domain: document.getElementById('runtimeAutoRegisterDomainInput').value.trim(),
            auto_create_workspace: document.getElementById('runtimeAutoCreateWorkspaceInput').checked,
            auto_select_workspace: document.getElementById('runtimeAutoSelectWorkspaceInput').checked,
            workspace_create_dry_run: document.getElementById('runtimeWorkspaceDryRunInput').checked,
            workspace_creation_template_space_id: document.getElementById('runtimeTemplateSpaceInput').value.trim(),
            account_probe_interval_seconds: Number(document.getElementById('runtimeProbeIntervalInput').value || 300),
            refresh_execution_mode: document.getElementById('runtimeRefreshExecutionModeInput').value || 'manual',
            refresh_request_url: document.getElementById('runtimeRefreshRequestUrlInput').value.trim(),
            refresh_client_id: document.getElementById('runtimeRefreshClientIdInput').value.trim(),
            refresh_client_secret: refreshClientSecretValue || (this._runtimeSecretPresence?.refresh_client_secret ? undefined : ''),
            workspace_execution_mode: document.getElementById('runtimeWorkspaceExecutionModeInput').value || 'manual',
            workspace_request_url: document.getElementById('runtimeWorkspaceRequestUrlInput').value.trim(),
            allow_real_probe_requests: document.getElementById('runtimeAllowRealProbeRequestsInput').checked,
            chat_enabled: document.getElementById('runtimeChatEnabledInput').checked,
        };

        try {
            await window.NotionAI.API.Admin.saveRuntimeSettings(payload);
            this.renderRuntimeConfigSummary(payload);
            this.renderRuntimeProxyHealth({
                mode: payload.upstream_proxy_mode,
                active: Boolean(payload.upstream_proxy || payload.upstream_http_proxy || payload.upstream_https_proxy || payload.upstream_socks5_proxy || (payload.upstream_warp_enabled && payload.upstream_warp_proxy)),
                warp_enabled: Boolean(payload.upstream_warp_enabled),
                warp_configured: Boolean(payload.upstream_warp_proxy),
                socks5_configured: Boolean(payload.upstream_socks5_proxy),
                http_configured: Boolean(payload.upstream_http_proxy),
                https_configured: Boolean(payload.upstream_https_proxy),
            });
            this.renderRegisterAutomationSummary({});
            this.renderRuntimeProxyChecks({});
            this.renderRuntimeOperationsPanel({});
            window.NotionAI.Core.State.set('chatEnabled', Boolean(payload.chat_enabled));
            if (typeof window.NotionAI.Core.App?.syncShellFromState === 'function') {
                window.NotionAI.Core.App.syncShellFromState();
            }
            const hint = document.getElementById('runtimeConfigHint');
            if (hint) {
                hint.textContent = `运行时配置已保存。后台探测间隔：${payload.account_probe_interval_seconds} 秒。自动创建工作区：${payload.auto_create_workspace ? '开启' : '关闭'}。`;
            }
            if (!silent) {
                this.setAdminNotice('运行时配置已保存。');
            }
        } catch (error) {
            this.setAdminNotice(error.message || '保存运行时配置失败。');
        }
    },

    async checkRuntimeProxyHealth() {
        try {
            const data = await window.NotionAI.API.Admin.getProxyHealth();
            this.renderRuntimeProxyHealth(data.summary || {});
            this.renderRuntimeProxyChecks(data.checks || {});
            this.renderRuntimeOperationsPanel({
                ...(this._lastRuntimeOperationsPanel || {}),
                proxy_mode: data?.summary?.mode || 'direct',
                proxy_operator_state: data?.summary?.operator_state || 'direct',
                reachable_proxy_targets: data?.summary?.reachable_targets || [],
            });
            const mode = String(data.response_mode || 'status_summary').trim().toLowerCase() || 'status_summary';
            this.setAdminNotice(`代理健康检查已完成，当前模式：${mode}。`);
        } catch (error) {
            this.setAdminNotice(error.message || '检查代理健康状态失败。');
        }
    },

    async triggerAutoRegisterNow() {
        try {
            const data = await window.NotionAI.API.Admin.triggerAutoRegisterNow();
            this.renderRegisterAutomationSummary({
                ...(this._lastRegisterAutomationSummary || {}),
                last_decision_reason: data.reason || (data.ok ? 'queued' : '未知'),
                current_reason: data.reason || (data.ok ? 'queued' : '未知'),
                last_task_id: data.task_id || '',
                active: Boolean(data.ok),
            });
            this.renderRegisterAutomationGuidance({
                reason: data.reason || (data.ok ? 'queued' : '未知'),
                severity: data.ok ? 'success' : 'warning',
                message: data.ok ? '自动注册任务已成功加入队列。' : `自动注册已跳过：${data.reason || '未知'}。`,
                next_step: data.ok ? '请先观察当前注册任务，再决定是否继续触发新的任务。' : '请检查运行时代理和自动注册设置。',
            });
            await this.refreshAutoRegisterStatus(true);
            this.setAdminNotice(data.ok ? '自动注册任务已加入队列。' : `自动注册已跳过：${data.reason || '未知'}。`);
        } catch (error) {
            this.setAdminNotice(error.message || '触发自动注册失败。');
        }
    },

    async refreshAutoRegisterStatus(silent = false) {
        try {
            const data = await window.NotionAI.API.Admin.getAutoRegisterStatus();
            this.renderRegisterAutomationSummary(data.automation || {});
            this.renderRegisterAutomationGuidance(data.guidance || {});
            this.renderRuntimeProxyHealth(data.proxy_health?.summary || (this._lastRuntimeProxyHealth || {}));
            this.renderRuntimeProxyChecks(data.proxy_health?.checks || {});
            this.renderRuntimeOperationsPanel(data.runtime_operations_panel || {});
            if (!silent) {
                this.setAdminNotice('自动注册状态已加载。');
            }
        } catch (error) {
            this.setAdminNotice(error.message || '加载自动注册状态失败。');
        }
    },

    clearAccountForm() {
        ['adminAccountIdInput', 'adminAccountTokenInput', 'adminAccountUserIdInput', 'adminAccountSpaceIdInput', 'adminAccountEmailInput', 'adminAccountPlanInput', 'adminAccountTagsInput', 'adminAccountNotesInput'].forEach((id) => {
            const element = document.getElementById(id);
            if (element) {
                element.value = '';
            }
        });
    },

    fillAccountForm(account) {
        if (!account || typeof account !== 'object') {
            return;
        }
        const tags = Array.isArray(account.tags) ? account.tags.join(', ') : '';
        const mappings = {
            adminAccountIdInput: account.id || '',
            adminAccountTokenInput: account.token_v2 || '',
            adminAccountUserIdInput: account.user_id || '',
            adminAccountSpaceIdInput: account.space_id || '',
            adminAccountEmailInput: account.user_email || '',
            adminAccountPlanInput: account.plan_type || '',
            adminAccountTagsInput: tags,
            adminAccountNotesInput: account.notes || '',
        };
        Object.entries(mappings).forEach(([id, value]) => {
            const element = document.getElementById(id);
            if (element) {
                element.value = value;
            }
        });
        this.setAdminNotice(`已将账号 ${account.user_email || account.user_id || account.id} 加载到编辑器。`);
    },

    async addAccountFromForm() {
        const accountId = document.getElementById('adminAccountIdInput').value.trim();
        const token_v2 = document.getElementById('adminAccountTokenInput').value.trim();
        const user_id = document.getElementById('adminAccountUserIdInput').value.trim();
        const space_id = document.getElementById('adminAccountSpaceIdInput').value.trim();
        const user_email = document.getElementById('adminAccountEmailInput').value.trim();
        const plan_type = document.getElementById('adminAccountPlanInput').value.trim() || '未知';
        const notes = document.getElementById('adminAccountNotesInput').value.trim();
        const tags = document.getElementById('adminAccountTagsInput').value
            .split(',')
            .map((item) => item.trim())
            .filter(Boolean);

        if (!token_v2 || !user_id || !space_id) {
            this.setAdminNotice('新增账号必须提供 token_v2、user_id 和 space_id。');
            return;
        }

        try {
            await window.NotionAI.API.Admin.trigger('/v1/admin/accounts', {
                ...(accountId ? { id: accountId } : {}),
                token_v2,
                user_id,
                space_id,
                user_email,
                plan_type,
                notes,
                tags,
            });
            this.clearAccountForm();
            await this.refreshAdminPanel(accountId ? '账号已更新。' : '账号已加入池中。');
        } catch (error) {
            this.setAdminNotice(error.message || '新增账号失败。');
        }
    },

    parseBulkAccountsInput() {
        const raw = document.getElementById('adminAccountBulkInput').value.trim();
        if (!raw) {
            throw new Error('请先粘贴 JSON 数组。');
        }
        const parsed = JSON.parse(raw);
        if (!Array.isArray(parsed)) {
            throw new Error('批量账号载荷必须是 JSON 数组。');
        }
        return parsed;
    },

    async bulkImportAccounts() {
        try {
            const accounts = this.parseBulkAccountsInput();
            await window.NotionAI.API.Admin.bulkImportAccounts(accounts);
            await this.refreshAdminPanel('批量导入已完成。');
        } catch (error) {
            this.setAdminNotice(error.message || '批量导入失败。');
        }
    },

    async bulkReplaceAccounts() {
        try {
            const accounts = this.parseBulkAccountsInput();
            await window.NotionAI.API.Admin.bulkReplaceAccounts(accounts);
            await this.refreshAdminPanel('批量替换已完成。');
        } catch (error) {
            this.setAdminNotice(error.message || '批量替换失败。');
        }
    },

    async exportAccountsToTextarea() {
        try {
            const data = await window.NotionAI.API.Admin.exportAccounts(false);
            const input = document.getElementById('adminAccountBulkInput');
            if (input) {
                input.value = JSON.stringify(data.accounts || [], null, 2);
            }
            this.setAdminNotice(`已从 ${data.storage?.accounts_path || '账号文件'} 导出 ${data.count || 0} 个账号。`);
        } catch (error) {
            this.setAdminNotice(error.message || '导出账号失败。');
        }
    },

    async bulkActionOnFiltered(action, successMessage) {
        try {
            const data = await window.NotionAI.API.Admin.loadSafeAccounts(this.getAdminFilters());
            const accountIds = (data.accounts || []).map((item) => item.id).filter(Boolean);
            if (!accountIds.length) {
                this.setAdminNotice('没有筛选出的账号匹配当前批量动作。');
                return;
            }
            const result = await window.NotionAI.API.Admin.bulkAccountAction(accountIds, action);
            this.renderBulkActionResult(result);
            const output = document.getElementById('requestTemplateOutput');
            if (output && (action === 'refresh_probe' || action === 'workspace_probe')) {
                output.value = JSON.stringify(result, null, 2);
            }
            await this.refreshAdminPanel(successMessage);
        } catch (error) {
            this.setAdminNotice(error.message || '批量动作执行失败。');
        }
    },

    async finalizeOAuthFromForm() {
        const callback = this.getOAuthCallbackParams();
        const payload = {
            token_v2: document.getElementById('oauthTokenInput').value.trim(),
            user_id: document.getElementById('oauthUserIdInput').value.trim(),
            space_id: document.getElementById('oauthSpaceIdInput').value.trim(),
            user_email: document.getElementById('oauthEmailInput').value.trim(),
            redirect_uri: document.getElementById('oauthRedirectUriInput').value.trim() || window.location.origin,
            access_token: callback.access_token,
            refresh_token: callback.refresh_token,
            expires_at: callback.expires_at ? Number(callback.expires_at) : undefined,
            state: callback.state,
        };

        if (!payload.token_v2 || !payload.user_id) {
            this.setAdminNotice('完成 OAuth 导入必须提供 token_v2 和 user_id。');
            return;
        }

        try {
            await window.NotionAI.API.Admin.finalizeOAuth(payload);
            document.getElementById('oauthTokenInput').value = '';
            document.getElementById('oauthUserIdInput').value = '';
            document.getElementById('oauthSpaceIdInput').value = '';
            document.getElementById('oauthEmailInput').value = '';
            document.getElementById('oauthRedirectUriInput').value = window.location.origin;
            await this.refreshAdminPanel('OAuth 账号已导入。');
        } catch (error) {
            this.setAdminNotice(error.message || '完成 OAuth 账号导入失败。');
        }
    },

    async parseAndFinalizeCallbackUrl() {
        const parsed = this.parseManualCallbackUrl();
        if (!parsed) {
            return;
        }
        await this.finalizeOAuthFromForm();
    },

    bindActionHistoryFilters() {
        ['adminActionHistoryAccountFilter', 'adminActionHistoryTypeFilter', 'adminActionHistoryStatusFilter', 'adminActionHistoryFailureFilter', 'adminActionHistoryReauthOnly'].forEach((id) => {
            const element = document.getElementById(id);
            if (!element || element.dataset.bound === 'true') {
                return;
            }
            element.dataset.bound = 'true';
            const eventName = element.tagName === 'INPUT' && element.type === 'text' ? 'input' : 'change';
            element.addEventListener(eventName, () => {
                if (id === 'adminActionHistoryAccountFilter') {
                    this.refreshAdminPanel('Action history account filter updated.');
                    return;
                }
                this.renderActionHistory(this._lastAdminSnapshot || {});
            });
        });
    },

    bindUsageFilters() {
        const applyBtn = document.getElementById('adminUsageApplyBtn');
        if (applyBtn && applyBtn.dataset.bound !== 'true') {
            applyBtn.dataset.bound = 'true';
            applyBtn.addEventListener('click', () => {
                this.refreshAdminPanel('Usage filters applied.');
            });
        }

        const clearBtn = document.getElementById('adminUsageClearBtn');
        if (clearBtn && clearBtn.dataset.bound !== 'true') {
            clearBtn.dataset.bound = 'true';
            clearBtn.addEventListener('click', () => {
                this.clearUsageFilters();
                this.refreshAdminPanel('Usage filters cleared.');
            });
        }

        ['adminUsageRequestTypeInput', 'adminUsageLimitInput'].forEach((id) => {
            const element = document.getElementById(id);
            if (!element || element.dataset.bound === 'true') {
                return;
            }
            element.dataset.bound = 'true';
            element.addEventListener('change', () => {
                this.refreshAdminPanel('Usage filters updated.');
            });
        });
    },

    bindConsoleNavigation() {
        document.querySelectorAll('[data-settings-scroll-target]').forEach((button) => {
            if (button.dataset.bound === 'true') {
                return;
            }
            button.dataset.bound = 'true';
            button.addEventListener('click', () => {
                const moduleName = button.dataset.module || '';
                if (moduleName && typeof window.NotionAI.Core.App?.setActiveModule === 'function') {
                    window.NotionAI.Core.App.setActiveModule(moduleName);
                }
                const targetId = button.dataset.settingsScrollTarget || '';
                const target = targetId ? document.getElementById(targetId) : null;
                if (!target) {
                    return;
                }
                target.scrollIntoView({ behavior: 'smooth', block: 'start' });
            });
        });
    }
};
