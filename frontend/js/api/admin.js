window.NotionAI = window.NotionAI || {};
window.NotionAI.API = window.NotionAI.API || {};

window.NotionAI.API.Admin = {
    async login(username, password) {
        const response = await window.NotionAI.API.Client.post('/v1/admin/login', { username, password }, {
            headers: { 'X-Admin-Session': '' }
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.detail || '后台登录失败');
        }
        window.NotionAI.Core.State.set('adminUsername', data.username || username);
        window.NotionAI.Core.State.set('adminPassword', '');
        window.NotionAI.Core.State.set('adminSessionToken', data.session_token || '');
        window.NotionAI.Core.State.set('adminSessionExpiresAt', Number(data.session_expires_at || 0));
        window.NotionAI.Core.State.set('adminMustChangePassword', Boolean(data.must_change_password));
        window.NotionAI.Core.State.persistAdminSession({
            username: data.username || username,
            sessionToken: data.session_token || '',
            sessionExpiresAt: Number(data.session_expires_at || 0),
            mustChangePassword: Boolean(data.must_change_password)
        });
        return data;
    },

    async changePassword(payload) {
        const response = await window.NotionAI.API.Client.post('/v1/admin/change-password', payload);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.detail || '更新后台凭证失败');
        }
        window.NotionAI.Core.State.set('adminUsername', data.username || 'admin');
        window.NotionAI.Core.State.set('adminPassword', '');
        window.NotionAI.Core.State.set('adminSessionToken', data.session_token || '');
        window.NotionAI.Core.State.set('adminSessionExpiresAt', Number(data.session_expires_at || 0));
        window.NotionAI.Core.State.set('adminMustChangePassword', false);
        window.NotionAI.Core.State.persistAdminSession({
            username: data.username || 'admin',
            sessionToken: data.session_token || '',
            sessionExpiresAt: Number(data.session_expires_at || 0),
            mustChangePassword: false
        });
        return data;
    },

    logout() {
        window.NotionAI.Core.State.clearAdminSession();
        window.NotionAI.Core.State.set('adminUsername', 'admin');
        window.NotionAI.Core.State.set('adminSessionExpiresAt', 0);
    },

    async loadSafeAccounts(filters = {}) {
        const params = new URLSearchParams();
        Object.entries(filters || {}).forEach(([key, value]) => {
            if (value !== undefined && value !== null && String(value).trim() !== '') {
                params.set(key, String(value));
            }
        });
        const query = params.toString();
        const endpoint = query ? `/v1/admin/accounts/safe?${query}` : '/v1/admin/accounts/safe';
        const response = await window.NotionAI.API.Client.get(endpoint);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.detail || '加载安全账号列表失败');
        }
        return data;
    },

    async trigger(endpoint, payload = {}) {
        const response = await window.NotionAI.API.Client.post(endpoint, payload);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.detail || '后台动作执行失败');
        }
        return data;
    },

    async getAccount(accountId, options = {}) {
        const params = new URLSearchParams();
        if (options.raw) {
            params.set('raw', 'true');
        }
        const query = params.toString();
        const endpoint = query ? `/v1/admin/accounts/${accountId}?${query}` : `/v1/admin/accounts/${accountId}`;
        const response = await window.NotionAI.API.Client.get(endpoint);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.detail || '加载账号失败');
        }
        return data;
    },

    async patchAccount(accountId, payload) {
        const response = await window.NotionAI.API.Client.patch(`/v1/admin/accounts/${accountId}`, payload);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.detail || '更新账号失败');
        }
        return data;
    },

    async toggleAccount(accountId, enabled) {
        const endpoint = enabled ? '/v1/admin/accounts/enable' : '/v1/admin/accounts/disable';
        return this.trigger(endpoint, { account_id: accountId });
    },

    async startEmailLogin(payload) {
        return this.trigger('/v1/admin/email-login/start', payload);
    },

    async finalizeEmailLogin(payload) {
        return this.trigger('/v1/admin/email-login/finalize', payload);
    },

    async runAccountAction(accountId, action) {
        return this.trigger(`/v1/admin/accounts/${accountId}/${action}`);
    },

    async deleteAccount(accountId) {
        const response = await window.NotionAI.API.Client.delete(`/v1/admin/accounts/${accountId}`);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.detail || '删除账号失败');
        }
        return data;
    },

    async loadConfig() {
        const response = await window.NotionAI.API.Client.get('/v1/admin/config');
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.detail || '加载后台配置失败');
        }
        return data;
    },

    async getChatAccess() {
        const response = await window.NotionAI.API.Client.get('/v1/chat/access');
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.detail || '加载聊天访问状态失败');
        }
        return data;
    },

    async loginChat(password) {
        const response = await window.NotionAI.API.Client.post('/v1/chat/login', { password }, {
            headers: { 'X-Chat-Session': '' }
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.detail || '聊天访问登录失败');
        }
        window.NotionAI.Core.State.persistChatSession({
            sessionToken: data.session_token || '',
            sessionExpiresAt: Number(data.session_expires_at || 0)
        });
        return data;
    },

    async uploadMedia(dataUrl, fileName = '') {
        const response = await window.NotionAI.API.Client.post('/v1/media/upload', {
            data_url: dataUrl,
            file_name: fileName
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.detail || '上传图片失败');
        }
        return data;
    },

    async saveRuntimeSettings(payload) {
        const response = await window.NotionAI.API.Client.put('/v1/admin/config/settings', payload);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.detail || '保存运行时设置失败');
        }
        return data;
    },

    async getProxyHealth() {
        const response = await window.NotionAI.API.Client.get('/v1/admin/config/proxy-health');
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.detail || '加载代理健康状态失败');
        }
        return data;
    },

    async triggerAutoRegisterNow() {
        return this.trigger('/v1/admin/register/auto-trigger');
    },

    async getAutoRegisterStatus() {
        const response = await window.NotionAI.API.Client.get('/v1/admin/register/auto-status');
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.detail || '加载自动注册状态失败');
        }
        return data;
    },

    async startManualRegister(payload) {
        const response = await window.NotionAI.API.Client.post('/v1/register/start', payload);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.detail || '启动手动注册任务失败');
        }
        return data;
    },

    async getRegisterTaskStatus(taskId) {
        const response = await window.NotionAI.API.Client.get(`/v1/register/status/${encodeURIComponent(taskId)}`);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.detail || '加载注册任务状态失败');
        }
        return data;
    },

    async getSessionRefreshStatus() {
        const response = await window.NotionAI.API.Client.get('/v1/admin/session/refresh-status');
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.detail || '加载会话刷新状态失败');
        }
        return data;
    },

    async getWorkspaceCreateStatus() {
        const response = await window.NotionAI.API.Client.get('/v1/admin/workspaces/create-status');
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.detail || '加载工作区创建状态失败');
        }
        return data;
    },

    async getSessionRefreshDiagnostics() {
        const response = await window.NotionAI.API.Client.get('/v1/admin/session/refresh-diagnostics');
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.detail || '加载会话刷新诊断失败');
        }
        return data;
    },

    async getWorkspaceDiagnostics() {
        const response = await window.NotionAI.API.Client.get('/v1/admin/workspaces/diagnostics');
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.detail || '加载工作区诊断失败');
        }
        return data;
    },

    async getRequestTemplates() {
        const response = await window.NotionAI.API.Client.get('/v1/admin/request-templates');
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.detail || '加载请求模板失败');
        }
        return data;
    },

    async getAccountRequestTemplates(accountId) {
        const response = await window.NotionAI.API.Client.get(`/v1/admin/accounts/${accountId}/request-templates`);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.detail || '加载账号请求模板失败');
        }
        return data;
    },

    async getAdminReport(filters = {}) {
        const params = new URLSearchParams();
        if (filters.action_account && String(filters.action_account).trim()) {
            params.set('action_account', String(filters.action_account).trim());
        }
        const query = params.toString();
        const response = await window.NotionAI.API.Client.get(query ? `/v1/admin/report?${query}` : '/v1/admin/report');
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.detail || '加载后台报告失败');
        }
        return data;
    },

    async getAdminSnapshot(filters = {}) {
        const params = new URLSearchParams();
        if (filters.action_account && String(filters.action_account).trim()) {
            params.set('action_account', String(filters.action_account).trim());
        }
        const query = params.toString();
        const response = await window.NotionAI.API.Client.get(query ? `/v1/admin/snapshot?${query}` : '/v1/admin/snapshot');
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.detail || '加载后台快照失败');
        }
        return data;
    },

    async runRefreshProbe(accountId) {
        return this.trigger(`/v1/admin/accounts/${accountId}/refresh-probe`);
    },

    async runWorkspaceProbe(accountId) {
        return this.trigger(`/v1/admin/accounts/${accountId}/workspace-probe`);
    },

    async retryRegisterHydration(accountId) {
        return this.trigger(`/v1/admin/accounts/${accountId}/register-hydration-retry`);
    },

    async exportAccounts(raw = false) {
        const endpoint = raw ? '/v1/admin/accounts/export?raw=true' : '/v1/admin/accounts/export';
        const response = await window.NotionAI.API.Client.get(endpoint);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.detail || '导出账号失败');
        }
        return data;
    },

    async bulkImportAccounts(accounts) {
        return this.trigger('/v1/admin/accounts/import', { accounts });
    },

    async bulkReplaceAccounts(accounts) {
        return this.trigger('/v1/admin/accounts/replace', { accounts });
    },

    async bulkAccountAction(accountIds, action) {
        return this.trigger('/v1/admin/accounts/bulk-action', {
            account_ids: accountIds,
            action,
        });
    },

    async loadAlerts() {
        const response = await window.NotionAI.API.Client.get('/v1/admin/alerts');
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.detail || '加载后台告警失败');
        }
        return data;
    },

    async getUsageSummary(filters = {}) {
        const params = new URLSearchParams();
        Object.entries(filters || {}).forEach(([key, value]) => {
            if (value !== undefined && value !== null && String(value).trim() !== '') {
                params.set(key, String(value));
            }
        });
        const query = params.toString();
        const response = await window.NotionAI.API.Client.get(query ? `/v1/admin/usage/summary?${query}` : '/v1/admin/usage/summary');
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.detail || '加载用量汇总失败');
        }
        return data;
    },

    async getUsageEvents(filters = {}) {
        const params = new URLSearchParams();
        Object.entries(filters || {}).forEach(([key, value]) => {
            if (value !== undefined && value !== null && String(value).trim() !== '') {
                params.set(key, String(value));
            }
        });
        const query = params.toString();
        const response = await window.NotionAI.API.Client.get(query ? `/v1/admin/usage/events?${query}` : '/v1/admin/usage/events');
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.detail || '加载用量事件失败');
        }
        return data;
    },

    async loadOperationLogs() {
        const response = await window.NotionAI.API.Client.get('/v1/admin/operations');
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.detail || '加载操作日志失败');
        }
        return data;
    }
};
