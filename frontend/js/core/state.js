/**
 * State Management Module
 * Manages global application state
 */

// Initialize namespace
window.NotionAI = window.NotionAI || {};
window.NotionAI.Core = window.NotionAI.Core || {};

window.NotionAI.Core.State = {
    // Global state object
    _state: {
        baseUrl: localStorage.getItem('claude_base_url') || window.location.origin,
        apiKey: '',
        adminUsername: sessionStorage.getItem('claude_admin_username') || 'admin',
        adminPassword: '',
        adminSessionToken: sessionStorage.getItem('claude_admin_session') || '',
        adminSessionExpiresAt: Number(sessionStorage.getItem('claude_admin_session_expires_at') || 0),
        adminMustChangePassword: false,
        activeModule: sessionStorage.getItem('claude_active_module') || '',
        chatEnabled: false,
        theme: localStorage.getItem('theme') || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'),
        chats: JSON.parse(localStorage.getItem('claude_chats')) || [],
        currentChatId: null,
        isGenerating: false,
        controller: null,
        modelDisplayNames: {},
        chatToRename: null,
        pendingAttachments: [],
        attachmentHint: ''
    },

    /**
     * Loads stored API key from localStorage or sessionStorage
     * @returns {string} API key or empty string
     */
    loadStoredApiKey() {
        const apiKey = localStorage.getItem('claude_api_key') ||
                      sessionStorage.getItem('claude_api_key') ||
                      '';
        return apiKey;
    },

    /**
     * Persists API key to both localStorage and sessionStorage
     * @param {string} apiKey - API key to store
     */
    persistApiKey(apiKey) {
        if (apiKey) {
            localStorage.setItem('claude_api_key', apiKey);
            sessionStorage.setItem('claude_api_key', apiKey);
        } else {
            localStorage.removeItem('claude_api_key');
            sessionStorage.removeItem('claude_api_key');
        }
    },

    persistAdminSession({ username = '', sessionToken = '', sessionExpiresAt = 0, mustChangePassword = false } = {}) {
        localStorage.removeItem('claude_admin_password');
        if (username) {
            sessionStorage.setItem('claude_admin_username', username);
        } else {
            sessionStorage.removeItem('claude_admin_username');
        }
        if (sessionToken) {
            sessionStorage.setItem('claude_admin_session', sessionToken);
        } else {
            sessionStorage.removeItem('claude_admin_session');
        }
        if (sessionExpiresAt) {
            sessionStorage.setItem('claude_admin_session_expires_at', String(sessionExpiresAt));
        } else {
            sessionStorage.removeItem('claude_admin_session_expires_at');
        }
        sessionStorage.setItem('claude_admin_must_change_password', mustChangePassword ? 'true' : 'false');
    },

    clearAdminSession() {
        localStorage.removeItem('claude_admin_password');
        sessionStorage.removeItem('claude_admin_password');
        sessionStorage.removeItem('claude_admin_username');
        sessionStorage.removeItem('claude_admin_session');
        sessionStorage.removeItem('claude_admin_session_expires_at');
        sessionStorage.removeItem('claude_admin_must_change_password');
        this._state.adminPassword = '';
        this._state.adminSessionToken = '';
        this._state.adminSessionExpiresAt = 0;
        this._state.adminMustChangePassword = false;
    },

    persistActiveModule(moduleName) {
        const normalized = String(moduleName || '').trim();
        if (normalized) {
            sessionStorage.setItem('claude_active_module', normalized);
        } else {
            sessionStorage.removeItem('claude_active_module');
        }
        this._state.activeModule = normalized;
    },

    /**
     * Gets a state value by key
     * @param {string} key - State key
     * @returns {*} State value
     */
    get(key) {
        return this._state[key];
    },

    /**
     * Sets a state value by key
     * @param {string} key - State key
     * @param {*} value - New value
     */
    set(key, value) {
        this._state[key] = value;
    },

    /**
     * Gets the entire state object (for compatibility)
     * @returns {Object} Complete state object
     */
    getState() {
        return this._state;
    }
};

// Initialize state with API key loading
window.NotionAI.Core.State._state.apiKey = window.NotionAI.Core.State.loadStoredApiKey();
