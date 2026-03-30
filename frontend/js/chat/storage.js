/**
 * Storage Module
 * Handles LocalStorage operations for chat data
 */

// Initialize namespace
window.NotionAI = window.NotionAI || {};
window.NotionAI.Chat = window.NotionAI.Chat || {};

window.NotionAI.Chat.Storage = {
    /**
     * Saves chats array to localStorage
     */
    saveChats() {
        const chats = window.NotionAI.Core.State.get('chats');
        localStorage.setItem('claude_chats', JSON.stringify(chats));
    },

    /**
     * Loads and sanitizes chats from localStorage
     * @returns {Array} Sanitized chats array
     */
    loadChats() {
        const chats = JSON.parse(localStorage.getItem('claude_chats')) || [];
        const sanitized = window.NotionAI.Utils.Validation.sanitizeChats(chats);
        window.NotionAI.Core.State.set('chats', sanitized);
        return sanitized;
    },

    /**
     * Adds a new message to current chat
     * @param {Object} message - Message object with role and content
     */
    addMessage(message) {
        const chats = window.NotionAI.Core.State.get('chats');
        const currentChatId = window.NotionAI.Core.State.get('currentChatId');
        const chat = chats.find(c => c.id === currentChatId);
        if (chat) {
            chat.messages.push(this.sanitizeMessageForStorage(message));
            window.NotionAI.Core.State.set('chats', chats);
            this.saveChats();
        }
    },

    sanitizeMessageForStorage(message) {
        if (!message || typeof message !== 'object') {
            return message;
        }
        const cloned = { ...message };
        if (Array.isArray(cloned.content)) {
            cloned.content = cloned.content.map((part) => {
                if (!part || typeof part !== 'object') {
                    return part;
                }
                if (part.type === 'image_url' && part.image_url && typeof part.image_url.url === 'string') {
                    return {
                        type: 'image_url',
                        image_url: { url: part.image_url.url },
                        media_id: typeof part.media_id === 'string' ? part.media_id : ''
                    };
                }
                if (part.type === 'text') {
                    return { type: 'text', text: String(part.text || '') };
                }
                return part;
            });
        }
        return cloned;
    },

    /**
     * Updates chat conversation ID
     * @param {string} chatId - Chat ID
     * @param {string} conversationId - Backend conversation ID
     */
    updateConversationId(chatId, conversationId) {
        const chats = window.NotionAI.Core.State.get('chats');
        const chat = chats.find(c => c.id === chatId);
        if (chat) {
            chat.conversationId = conversationId;
            window.NotionAI.Core.State.set('chats', chats);
            this.saveChats();
        }
    },

    /**
     * Deletes a chat by ID
     * @param {string} chatId - Chat ID to delete
     */
    deleteChat(chatId) {
        let chats = window.NotionAI.Core.State.get('chats');
        chats = chats.filter(c => c.id !== chatId);
        window.NotionAI.Core.State.set('chats', chats);
        this.saveChats();
    },

    /**
     * Updates chat title
     * @param {string} chatId - Chat ID
     * @param {string} title - New title
     */
    updateChatTitle(chatId, title) {
        const chats = window.NotionAI.Core.State.get('chats');
        const chat = chats.find(c => c.id === chatId);
        if (chat) {
            chat.title = title;
            window.NotionAI.Core.State.set('chats', chats);
            this.saveChats();
        }
    },

    /**
     * Toggles chat star status
     * @param {string} chatId - Chat ID
     */
    toggleStar(chatId) {
        const chats = window.NotionAI.Core.State.get('chats');
        const chat = chats.find(c => c.id === chatId);
        if (chat) {
            chat.starred = !chat.starred;
            window.NotionAI.Core.State.set('chats', chats);
            this.saveChats();
        }
    }
};
