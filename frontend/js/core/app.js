/**
 * Application Entry Point
 * Initializes the application and binds all event handlers
 */

// Initialize namespace
window.NotionAI = window.NotionAI || {};

// Global STATE for backward compatibility
const STATE = window.NotionAI.Core.State.getState();
let memoryDegradedNotified = false;

window.NotionAI.Core.App = window.NotionAI.Core.App || {
    getDefaultModule() {
        return window.NotionAI.Core.State.get('adminSessionToken') ? 'overview' : 'access';
    },

    getModuleTitle(moduleName) {
        const titles = {
            access: 'Admin access',
            overview: 'Overview',
            usage: 'Usage',
            accounts: 'Accounts',
            runtime: 'Runtime',
            diagnostics: 'Diagnostics',
            chat: 'Chat'
        };
        return titles[moduleName] || 'Admin workspace';
    },

    setActiveModule(moduleName) {
        const normalized = String(moduleName || '').trim();
        const requested = normalized || this.getDefaultModule();
        const chatEnabled = Boolean(window.NotionAI.Core.State.get('chatEnabled'));
        const nextModule = requested === 'chat' && !chatEnabled ? this.getDefaultModule() : requested;
        window.NotionAI.Core.State.persistActiveModule(nextModule);
        this.syncShellFromState();
    },

    syncShellFromState() {
        const activeModule = window.NotionAI.Core.State.get('activeModule') || this.getDefaultModule();
        const chatEnabled = Boolean(window.NotionAI.Core.State.get('chatEnabled'));
        const resolvedModule = activeModule === 'chat' && !chatEnabled ? this.getDefaultModule() : activeModule;
        if (resolvedModule !== activeModule) {
            window.NotionAI.Core.State.persistActiveModule(resolvedModule);
        }

        const adminView = document.getElementById('adminWorkspaceView');
        const chatView = document.getElementById('chatWorkspaceView');
        const chatBtn = document.getElementById('moduleChatBtn');
        const chatSidebarPanel = document.getElementById('chatSidebarPanel');
        const headerTitle = document.getElementById('headerTitle');

        if (chatBtn) {
            chatBtn.classList.toggle('hidden', !chatEnabled);
        }
        if (chatSidebarPanel) {
            chatSidebarPanel.classList.toggle('hidden', resolvedModule !== 'chat');
        }
        if (adminView) {
            adminView.classList.toggle('hidden', resolvedModule === 'chat');
        }
        if (chatView) {
            chatView.classList.toggle('hidden', resolvedModule !== 'chat');
        }
        if (headerTitle) {
            headerTitle.textContent = this.getModuleTitle(resolvedModule);
            headerTitle.classList.remove('opacity-0');
        }

        document.querySelectorAll('[data-module]').forEach((button) => {
            button.dataset.active = button.dataset.module === resolvedModule ? 'true' : 'false';
        });
    }
};

/**
 * Main application initialization
 */
function init() {
    window.NotionAI.Chat.Storage.loadChats();
    window.NotionAI.Chat.Storage.saveChats();
    window.NotionAI.UI.Theme.init();
    window.NotionAI.API.Models.loadModels();
    window.NotionAI.API.Settings.consumeOAuthCallbackParams();
    window.NotionAI.API.Settings.autoFinalizeOAuthIfPossible();
    window.NotionAI.API.Settings.bindActionHistoryFilters();
    window.NotionAI.API.Settings.bindUsageFilters();
    window.NotionAI.API.Settings.bindConsoleNavigation();

    window.NotionAI.Chat.Manager.renderChatList();
    updateWelcomeGreeting();

    bindEventListeners();
    populateModels();

    const initialModule = window.NotionAI.Core.State.get('activeModule') || window.NotionAI.Core.App.getDefaultModule();
    window.NotionAI.Core.App.setActiveModule(initialModule);
    window.NotionAI.API.Settings.loadRuntimeConfigIntoForm();
    if (window.NotionAI.Core.State.get('adminSessionToken')) {
        window.NotionAI.API.Settings.refreshAdminPanel('当前浏览器会话的 admin session 已恢复。');
    }
}

/**
 * Binds all event listeners
 */
function bindEventListeners() {
    // Theme toggle
    document.getElementById('themeToggleBtn').addEventListener('click', () => {
        window.NotionAI.UI.Theme.toggle();
    });

    // Module navigation
    document.querySelectorAll('[data-module]').forEach((button) => {
        button.addEventListener('click', () => {
            const moduleName = button.dataset.module || '';
            if (!moduleName) {
                return;
            }
            window.NotionAI.Core.App.setActiveModule(moduleName);
            if (moduleName === 'chat' && !STATE.currentChatId) {
                window.NotionAI.Chat.Manager.startNewChat();
            }
            if (window.innerWidth < 768) {
                window.NotionAI.UI.Sidebar.close();
            }
        });
    });

    // New chat
    document.getElementById('newChatBtn').addEventListener('click', () => {
        window.NotionAI.Core.App.setActiveModule('chat');
        window.NotionAI.Chat.Manager.startNewChat();
    });

    // Sidebar
    document.getElementById('openSidebarBtn').addEventListener('click', () => {
        window.NotionAI.UI.Sidebar.open();
    });
    document.getElementById('closeSidebarBtn').addEventListener('click', () => {
        window.NotionAI.UI.Sidebar.close();
    });
    document.getElementById('mobileBackdrop').addEventListener('click', () => {
        window.NotionAI.UI.Sidebar.close();
    });
    document.getElementById('imagePreviewClose').addEventListener('click', () => {
        const modal = document.getElementById('imagePreviewModal');
        modal.classList.add('hidden');
        modal.classList.remove('flex');
    });
    document.getElementById('imagePreviewModal').addEventListener('click', (e) => {
        if (e.target.id === 'imagePreviewModal') {
            const modal = document.getElementById('imagePreviewModal');
            modal.classList.add('hidden');
            modal.classList.remove('flex');
        }
    });

    // Input
    document.getElementById('chatInput').addEventListener('input', () => {
        window.NotionAI.UI.Input.autoResize();
    });
    document.getElementById('chatInput').addEventListener('keydown', (e) => {
        window.NotionAI.UI.Input.handleKeydown(e, handleSend);
    });
    document.getElementById('sendBtn').addEventListener('click', handleSend);
    document.getElementById('imageUploadBtn').addEventListener('click', () => {
        document.getElementById('imageUploadInput').click();
    });
    document.getElementById('imageUploadInput').addEventListener('change', async (e) => {
        try {
            await window.NotionAI.UI.Input.addFiles(e.target.files);
        } catch (error) {
            console.error('Image upload failed:', error);
            window.NotionAI.UI.Input.setAttachmentHint(error.message || 'Image upload failed.');
        }
    });
    const composer = document.getElementById('chatInput').closest('.w-full');
    if (composer) {
        composer.addEventListener('dragover', (e) => {
            e.preventDefault();
        });
        composer.addEventListener('drop', async (e) => {
            e.preventDefault();
            try {
                await window.NotionAI.UI.Input.addFiles(e.dataTransfer?.files || []);
            } catch (error) {
                console.error('Drag upload failed:', error);
                window.NotionAI.UI.Input.setAttachmentHint(error.message || 'Image drag upload failed.');
            }
        });
    }

    // Memory banner
    document.getElementById('memoryBannerClose').addEventListener('click', () => {
        document.getElementById('memoryBanner').classList.add('hidden');
    });

    // Settings
    document.getElementById('cancelSettingsBtn').addEventListener('click', () => {
        window.NotionAI.Core.App.setActiveModule(window.NotionAI.Core.App.getDefaultModule());
    });
    document.getElementById('saveSettingsBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.save();
    });
    document.getElementById('adminUpdateCredentialsBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.updateAdminCredentialsOnly();
    });
    document.getElementById('adminSignOutBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.signOutAdminSession();
    });
    document.getElementById('adminRefreshBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.refreshAdminPanel('后台面板已刷新。');
    });
    document.getElementById('adminApplyFiltersBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.refreshAdminPanel('筛选条件已应用。');
    });
    document.getElementById('adminClearFiltersBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.clearAdminFilters();
        window.NotionAI.API.Settings.refreshAdminPanel('筛选条件已清空。');
    });
    document.getElementById('adminQuickInvalidBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.applyQuickFilter('invalid');
    });
    document.getElementById('adminQuickProbeFailuresBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.applyQuickFilter('probe_failures');
    });
    document.getElementById('adminQuickRefreshBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.applyQuickFilter('needs_refresh');
    });
    document.getElementById('adminQuickWorkspaceBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.applyQuickFilter('no_workspace');
    });
    document.getElementById('adminQuickPendingHydrationBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.applyPendingHydrationFilter();
    });
    document.getElementById('adminQuickHydrationDueBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.applyHydrationDueFilter();
    });
    document.getElementById('adminQuickEducationBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.applyQuickFilter('education');
    });
    document.getElementById('adminQuickUsableBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.applyQuickFilter('usable');
    });
    document.getElementById('adminBulkProbeBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.bulkActionOnFiltered('probe', '批量探测已完成。');
    });
    document.getElementById('adminBulkRefreshBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.bulkActionOnFiltered('refresh', '批量刷新已完成。');
    });
    document.getElementById('adminBulkRefreshProbeBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.bulkActionOnFiltered('refresh_probe', '批量刷新探测已完成。');
    });
    document.getElementById('adminBulkEnableBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.bulkActionOnFiltered('enable', '批量启用已完成。');
    });
    document.getElementById('adminBulkDisableBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.bulkActionOnFiltered('disable', '批量停用已完成。');
    });
    document.getElementById('adminBulkSyncWorkspaceBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.bulkActionOnFiltered('sync_workspace', '批量工作区同步已完成。');
    });
    document.getElementById('adminBulkHydrationRetryBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.bulkActionOnFiltered('register_hydration_retry', '批量补全重试已完成。');
    });
    document.getElementById('adminBulkCreateWorkspaceBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.bulkActionOnFiltered('create_workspace', '已触发批量创建工作区。');
    });
    document.getElementById('adminBulkWorkspaceProbeBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.bulkActionOnFiltered('workspace_probe', '批量工作区探测已完成。');
    });
    document.getElementById('adminProbeBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.runAdminAction('/v1/admin/accounts/probe', '探测已完成。');
    });
    document.getElementById('adminRefreshTokensBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.runAdminAction('/v1/admin/accounts/refresh', '刷新尝试已完成。');
    });
    document.getElementById('adminWorkspaceSyncBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.runAdminAction('/v1/admin/accounts/workspaces/sync', '工作区同步已完成。');
    });
    document.getElementById('adminWorkspaceCreateBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.runAdminAction('/v1/admin/accounts/workspaces/create', '已触发工作区创建流程。');
    });
    document.getElementById('adminAddAccountBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.addAccountFromForm();
    });
    document.getElementById('adminClearAccountFormBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.clearAccountForm();
    });
    document.getElementById('adminImportAccountsBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.bulkImportAccounts();
    });
    document.getElementById('adminReplaceAccountsBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.bulkReplaceAccounts();
    });
    document.getElementById('adminExportAccountsBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.exportAccountsToTextarea();
    });
    document.getElementById('adminPrevPageBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.changeAdminPage(-1);
    });
    document.getElementById('adminNextPageBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.changeAdminPage(1);
    });
    document.getElementById('runtimeLoadBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.loadRuntimeConfigIntoForm();
    });
    document.getElementById('runtimeSaveBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.saveRuntimeConfigFromForm();
    });
    document.getElementById('runtimeCheckProxyBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.checkRuntimeProxyHealth();
    });
    document.getElementById('runtimeTriggerAutoRegisterBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.triggerAutoRegisterNow();
    });
    document.getElementById('runtimeRefreshAutoRegisterBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.refreshAutoRegisterStatus();
    });
    document.getElementById('runtimeToggleSecretsBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.toggleRuntimeSecrets();
    });
    document.getElementById('runtimeAdvancedToggleBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.toggleRuntimeAdvanced();
    });
    document.getElementById('oauthStartBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.startOAuthFlow();
    });
    document.getElementById('oauthRefreshStatusBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.loadOAuthRefreshStatus();
    });
    document.getElementById('workspaceCreateStatusBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.loadWorkspaceCreateStatus();
    });
    document.getElementById('oauthRefreshDiagnosticsBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.loadOAuthRefreshDiagnostics();
    });
    document.getElementById('workspaceDiagnosticsBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.loadWorkspaceDiagnostics();
    });
    document.getElementById('requestTemplatesBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.loadRequestTemplates();
    });
    document.getElementById('showGenericTemplatesBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.loadRequestTemplates();
    });
    document.getElementById('copyRequestTemplateBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.copyRequestTemplateOutput();
    });
    document.getElementById('adminReportBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.loadAdminReport();
    });
    document.getElementById('adminSnapshotBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.loadAdminSnapshot();
    });
    document.getElementById('oauthFinalizeBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.finalizeOAuthFromForm();
    });
    document.getElementById('oauthParseCallbackBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.parseManualCallbackUrl();
    });
    document.getElementById('oauthImportCallbackBtn').addEventListener('click', () => {
        window.NotionAI.API.Settings.parseAndFinalizeCallbackUrl();
    });

    // Rename modal
    document.getElementById('cancelRenameBtn').addEventListener('click', () => {
        window.NotionAI.UI.Modal.closeRenameModal();
    });
    document.getElementById('saveRenameBtn').addEventListener('click', () => {
        window.NotionAI.UI.Modal.saveRename();
    });
    document.getElementById('renameModalInput').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            window.NotionAI.UI.Modal.saveRename();
        }
        if (e.key === 'Escape') {
            window.NotionAI.UI.Modal.closeRenameModal();
        }
    });

    // Model dropdown
    document.getElementById('modelTriggerBtn').addEventListener('click', (e) => {
        e.stopPropagation();
        toggleModelDropdown();
    });

    document.addEventListener('click', (e) => {
        const dropdown = document.getElementById('customModelDropdown');
        if (!dropdown.contains(e.target) && e.target.id !== 'modelTriggerBtn') {
            dropdown.classList.remove('open');
        }
    });
}

/**
 * Populates model dropdown with available models
 */
function populateModels() {
    const modelList = document.getElementById('simpleModelList');
    modelList.innerHTML = '';

    const models = window.NotionAI.API.Models.getAvailableModels();
    const currentModel = window.NotionAI.API.Models.getCurrentModel();

    models.forEach(model => {
        const isSelected = model.id === currentModel;
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'w-full text-left py-2 px-3 rounded-md hover:bg-black/5 dark:hover:bg-white/5 text-[14px] transition-colors flex justify-between items-center group';

        const contentWrapper = document.createElement('div');
        contentWrapper.className = 'flex flex-col items-start gap-0.5';

        const titleRow = document.createElement('div');
        titleRow.className = 'flex items-center gap-2';

        const labelSpan = document.createElement('span');
        labelSpan.textContent = model.label;
        if (isSelected) {
            labelSpan.className = 'font-medium';
        }
        titleRow.appendChild(labelSpan);

        // Add Beta badge for Gemini 3.1 and GPT 5.4
        const needsBeta = model.label.toLowerCase().includes('gemini') || model.label.toLowerCase().includes('5.4');
        if (needsBeta) {
            const betaBadge = document.createElement('span');
            betaBadge.className = 'model-beta-badge';
            betaBadge.textContent = 'Beta';
            titleRow.appendChild(betaBadge);
        }

        contentWrapper.appendChild(titleRow);

        // Add descriptions
        if (model.label.toLowerCase().includes('sonnet') && model.label.includes('4.6')) {
            const desc = document.createElement('div');
            desc.className = 'model-desc';
            desc.textContent = 'Most efficient for everyday tasks';
            contentWrapper.appendChild(desc);
        } else if (model.label.toLowerCase().includes('gemini')) {
            const desc = document.createElement('div');
            desc.className = 'model-desc';
            desc.textContent = 'Smart but Think longer';
            contentWrapper.appendChild(desc);
        }

        btn.appendChild(contentWrapper);

        if (isSelected) {
            const checkIcon = document.createElement('span');
            checkIcon.className = 'text-blue-500';
            checkIcon.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>';
            btn.appendChild(checkIcon);
        } else {
            const emptySpace = document.createElement('span');
            emptySpace.className = 'opacity-0 scale-75 group-hover:opacity-20 transition-all';
            emptySpace.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>';
            btn.appendChild(emptySpace);
        }

        btn.onclick = (e) => {
            e.stopPropagation();
            handleModelSelect(model.id, model.label);
        };

        modelList.appendChild(btn);
    });
}

/**
 * Handles model selection
 * @param {string} modelId - Model ID
 * @param {string} label - Model label
 */
function handleModelSelect(modelId, label) {
    window.NotionAI.API.Models.setCurrentModel(modelId, label);
    document.getElementById('modelTriggerText').textContent = label;
    document.getElementById('customModelDropdown').classList.remove('open');
    populateModels();
}

/**
 * Toggles model dropdown visibility
 */
function toggleModelDropdown() {
    document.getElementById('customModelDropdown').classList.toggle('open');
}

/**
 * Handles sending chat messages
 */
async function handleSend() {
    if (STATE.isGenerating) return;

    const content = window.NotionAI.UI.Input.buildMessageContent();
    const plainText = typeof content === 'string'
        ? content
        : window.NotionAI.Chat.Renderer.getUserMessageText(content);
    if (!plainText && (!Array.isArray(content) || !content.length)) return;

    // Get or create chat
    let chat = STATE.chats.find(c => c.id === STATE.currentChatId);
    const isNewChat = !chat;

    if (isNewChat) {
        chat = {
            id: STATE.currentChatId,
            title: plainText.length > 12 ? plainText.substring(0, 12) + '...' : (plainText || 'Image chat'),
            messages: [],
            conversationId: null
        };
        STATE.chats.push(chat);

        document.getElementById('headerTitle').textContent = chat.title;
        document.getElementById('headerTitle').classList.remove('opacity-0');
    }

    // Update UI
    window.NotionAI.UI.Input.clear();
    document.getElementById('welcomeScreen').classList.add('hidden');

    const inputWrapper = document.getElementById('inputAreaWrapper');
    inputWrapper.classList.remove('initial-state-container');
    inputWrapper.classList.add('chat-state-container');

    const selectorContainer = document.querySelector('.model-selector-container');
    if (selectorContainer) {
        selectorContainer.classList.remove('dropdown-down');
        selectorContainer.classList.add('dropdown-up');
    }

    document.getElementById('inputBgMask').classList.remove('opacity-0');
    document.getElementById('inputGradientMask').classList.remove('opacity-0');

    // Add user message
    chat.messages.push(window.NotionAI.Chat.Storage.sanitizeMessageForStorage({ role: 'user', content }));
    window.NotionAI.Chat.Renderer.appendMessage('user', content, true);
    window.NotionAI.Chat.Storage.saveChats();
    window.NotionAI.Chat.Manager.renderChatList();
    window.NotionAI.Utils.DOM.scrollToBottom();

    // Get selected model
    const selectedModel = window.NotionAI.API.Models.getCurrentModel();
    const selectedModelDisplayName = window.NotionAI.API.Models.getCurrentModelLabel();

    // Create AI message wrapper
    const aiWrapper = window.NotionAI.Chat.Renderer.appendMessage('assistant', '', false, selectedModelDisplayName);
    window.NotionAI.Utils.DOM.scrollToBottom();

    // Set generating state
    STATE.isGenerating = true;
    window.NotionAI.UI.Input.disable();

    try {
        // Stream response
        const result = await window.NotionAI.Chat.Streaming.streamResponse(chat, selectedModel, aiWrapper);

        // Save AI message
        const normalizedSearch = window.NotionAI.Utils.Validation.normalizeSearchPayload(result.searchState);
        const hasThinking = result.thinkingText.trim().length > 0;
        const hasSearch = (normalizedSearch.queries.length + normalizedSearch.sources.length) > 0;

        if (result.fullAiReply.trim()) {
            window.NotionAI.Chat.Renderer.updateAIMessage(aiWrapper, result.fullAiReply, true);
            chat.messages.push({
                role: 'assistant',
                content: result.fullAiReply,
                thinking: result.thinkingText,
                search: normalizedSearch,
                modelDisplayName: selectedModelDisplayName
            });
            window.NotionAI.Chat.Storage.saveChats();
        } else if (hasThinking || hasSearch) {
            window.NotionAI.Chat.Renderer.updateAIMessage(aiWrapper, '*No visible response received.*', true);
            chat.messages.push({
                role: 'assistant',
                content: '',
                thinking: result.thinkingText,
                search: normalizedSearch,
                modelDisplayName: selectedModelDisplayName
            });
            window.NotionAI.Chat.Storage.saveChats();
        } else {
            window.NotionAI.Chat.Renderer.updateAIMessage(aiWrapper, '*No visible response received.*', true);
        }

    } catch (err) {
        if (err.name !== 'AbortError') {
            console.error('API Error:', err);
        }
    } finally {
        STATE.isGenerating = false;
        window.NotionAI.UI.Input.enable();
        window.NotionAI.UI.Input.focus();
        STATE.controller = null;
    }
}

/**
 * Updates welcome greeting based on time of day
 */
function updateWelcomeGreeting() {
    const greetingEl = document.getElementById('welcomeGreeting');
    if (!greetingEl) return;

    // Get current time in China Standard Time (UTC+8)
    const now = new Date();
    const utc = now.getTime() + (now.getTimezoneOffset() * 60000);
    const cstDate = new Date(utc + (3600000 * 8));
    const hour = cstDate.getHours();
    const minute = cstDate.getMinutes();
    const timeStr = hour + minute / 60;

    let greeting = window.NotionAI.Core.Constants.GREETINGS.GOLDEN_HOUR;
    if (timeStr >= 5 && timeStr < 9) {
        greeting = window.NotionAI.Core.Constants.GREETINGS.EARLY_MORNING;
    } else if (timeStr >= 9 && timeStr < 11.5) {
        greeting = window.NotionAI.Core.Constants.GREETINGS.MORNING;
    } else if (timeStr >= 11.5 && timeStr < 13.5) {
        greeting = window.NotionAI.Core.Constants.GREETINGS.MIDDAY;
    } else if (timeStr >= 13.5 && timeStr < 17) {
        greeting = window.NotionAI.Core.Constants.GREETINGS.AFTERNOON;
    } else if (timeStr >= 17 && timeStr < 19) {
        greeting = window.NotionAI.Core.Constants.GREETINGS.GOLDEN_HOUR;
    } else if (timeStr >= 19 && timeStr < 22) {
        greeting = window.NotionAI.Core.Constants.GREETINGS.EVENING;
    } else if (timeStr >= 22 || timeStr < 1) {
        greeting = window.NotionAI.Core.Constants.GREETINGS.NIGHT_OWL;
    } else if (timeStr >= 1 && timeStr < 5) {
        greeting = window.NotionAI.Core.Constants.GREETINGS.LATE_NIGHT;
    }

    greetingEl.textContent = greeting;
}

// Update greeting every minute
setInterval(updateWelcomeGreeting, 60000);

// Initialize app when DOM is ready
window.addEventListener('DOMContentLoaded', init);
