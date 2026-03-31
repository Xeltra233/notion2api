/**
 * API Client Module
 * Handles all backend API communication
 */

// Initialize namespace
window.NotionAI = window.NotionAI || {};
window.NotionAI.API = window.NotionAI.API || {};

window.NotionAI.API.Client = {
    buildAuthHeaders(extraHeaders = {}) {
        const apiKey = String(window.NotionAI.Core.State.get('apiKey') || '').trim();
        const baseUrl = String(window.NotionAI.Core.State.get('baseUrl') || '').trim();
        const currentOrigin = String(window.location.origin || '').trim();
        const targetOrigin = (() => {
            try {
                return new URL(baseUrl || currentOrigin, currentOrigin).origin;
            } catch (error) {
                return currentOrigin;
            }
        })();
        const shouldAttachApiKey = Boolean(apiKey) && targetOrigin !== currentOrigin;
        return {
            ...(shouldAttachApiKey ? { 'Authorization': `Bearer ${apiKey}` } : {}),
            ...extraHeaders,
        };
    },
    /**
     * Makes a POST request to the API
     * @param {string} endpoint - API endpoint path
     * @param {Object} data - Request payload
     * @param {Object} options - Fetch options
     * @returns {Promise<Response>} Fetch response
     */
    async post(endpoint, data, options = {}) {
        const baseUrl = window.NotionAI.Core.State.get('baseUrl');
        const adminSessionToken = window.NotionAI.Core.State.get('adminSessionToken');
        const chatSessionToken = window.NotionAI.Core.State.get('chatSessionToken');
        const { headers: extraHeaders = {}, ...restOptions } = options || {};

        const response = await fetch(`${baseUrl}${endpoint}`, {
            method: 'POST',
            ...restOptions,
            headers: {
                'Content-Type': 'application/json',
                'X-Client-Type': window.NotionAI.Core.Constants.CLIENT_TYPE,
                ...(adminSessionToken ? { 'X-Admin-Session': adminSessionToken } : {}),
                ...(chatSessionToken ? { 'X-Chat-Session': chatSessionToken } : {}),
                ...this.buildAuthHeaders(extraHeaders)
            },
            body: JSON.stringify(data)
        });

        return response;
    },

    /**
     * Makes a DELETE request to the API
     * @param {string} endpoint - API endpoint path
     * @returns {Promise<Response>} Fetch response
     */
    async delete(endpoint) {
        const baseUrl = window.NotionAI.Core.State.get('baseUrl');
        const adminSessionToken = window.NotionAI.Core.State.get('adminSessionToken');
        const chatSessionToken = window.NotionAI.Core.State.get('chatSessionToken');

        const response = await fetch(`${baseUrl}${endpoint}`, {
            method: 'DELETE',
            headers: {
                'X-Client-Type': window.NotionAI.Core.Constants.CLIENT_TYPE,
                ...(adminSessionToken ? { 'X-Admin-Session': adminSessionToken } : {}),
                ...(chatSessionToken ? { 'X-Chat-Session': chatSessionToken } : {}),
                ...this.buildAuthHeaders()
            }
        });

        return response;
    },

    async get(endpoint, options = {}) {
        const baseUrl = window.NotionAI.Core.State.get('baseUrl');
        const adminSessionToken = window.NotionAI.Core.State.get('adminSessionToken');
        const chatSessionToken = window.NotionAI.Core.State.get('chatSessionToken');
        const { headers: extraHeaders = {}, ...restOptions } = options || {};

        const response = await fetch(`${baseUrl}${endpoint}`, {
            method: 'GET',
            ...restOptions,
            headers: {
                'X-Client-Type': window.NotionAI.Core.Constants.CLIENT_TYPE,
                ...(adminSessionToken ? { 'X-Admin-Session': adminSessionToken } : {}),
                ...(chatSessionToken ? { 'X-Chat-Session': chatSessionToken } : {}),
                ...this.buildAuthHeaders(extraHeaders)
            }
        });

        return response;
    },

    async patch(endpoint, data, options = {}) {
        const baseUrl = window.NotionAI.Core.State.get('baseUrl');
        const adminSessionToken = window.NotionAI.Core.State.get('adminSessionToken');
        const chatSessionToken = window.NotionAI.Core.State.get('chatSessionToken');
        const { headers: extraHeaders = {}, ...restOptions } = options || {};

        const response = await fetch(`${baseUrl}${endpoint}`, {
            method: 'PATCH',
            ...restOptions,
            headers: {
                'Content-Type': 'application/json',
                'X-Client-Type': window.NotionAI.Core.Constants.CLIENT_TYPE,
                ...(adminSessionToken ? { 'X-Admin-Session': adminSessionToken } : {}),
                ...(chatSessionToken ? { 'X-Chat-Session': chatSessionToken } : {}),
                ...this.buildAuthHeaders(extraHeaders)
            },
            body: JSON.stringify(data)
        });

        return response;
    },

    async put(endpoint, data, options = {}) {
        const baseUrl = window.NotionAI.Core.State.get('baseUrl');
        const adminSessionToken = window.NotionAI.Core.State.get('adminSessionToken');
        const chatSessionToken = window.NotionAI.Core.State.get('chatSessionToken');
        const { headers: extraHeaders = {}, ...restOptions } = options || {};

        const response = await fetch(`${baseUrl}${endpoint}`, {
            method: 'PUT',
            ...restOptions,
            headers: {
                'Content-Type': 'application/json',
                'X-Client-Type': window.NotionAI.Core.Constants.CLIENT_TYPE,
                ...(adminSessionToken ? { 'X-Admin-Session': adminSessionToken } : {}),
                ...(chatSessionToken ? { 'X-Chat-Session': chatSessionToken } : {}),
                ...this.buildAuthHeaders(extraHeaders)
            },
            body: JSON.stringify(data)
        });

        return response;
    },

    /**
     * Checks response for memory degradation status
     * @param {Response} response - Fetch response object
     * @returns {boolean} True if memory is degraded
     */
    checkMemoryStatus(response) {
        const memoryStatus = (response.headers.get('X-Memory-Status') || '').toLowerCase();
        return memoryStatus === 'degraded';
    },

    /**
     * Extracts conversation ID from response headers
     * @param {Response} response - Fetch response object
     * @returns {string} Conversation ID or empty string
     */
    getConversationId(response) {
        return (response.headers.get('X-Conversation-Id') || '').trim();
    },

    /**
     * Deletes a conversation from the backend
     * @param {string} conversationId - Backend conversation ID
     * @returns {Promise<boolean>} True if successful
     */
    async deleteConversation(conversationId) {
        try {
            const response = await this.delete(
                window.NotionAI.Core.Constants.API.DELETE_CONVERSATION(conversationId)
            );

            if (!response.ok && response.status !== 404) {
                console.error('Delete conversation failed:', response.status);
                return false;
            }
            return true;
        } catch (err) {
            console.error('Delete conversation request failed:', err);
            return false;
        }
    }
};
