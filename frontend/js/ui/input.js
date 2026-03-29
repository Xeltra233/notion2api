window.NotionAI = window.NotionAI || {};
window.NotionAI.UI = window.NotionAI.UI || {};

window.NotionAI.UI.Input = {
    MAX_ATTACHMENT_COUNT: 4,
    MAX_IMAGE_BYTES: 3 * 1024 * 1024,
    MAX_EDGE: 1600,
    JPEG_QUALITY: 0.82,

    setAttachmentHint(message) {
        const text = String(message || '');
        window.NotionAI.Core.State.set('attachmentHint', text);
        const hint = document.getElementById('attachmentHint');
        if (hint) {
            hint.textContent = text;
        }
    },

    autoResize() {
        const input = document.getElementById('chatInput');
        input.style.height = '56px';
        const scrollHeight = input.scrollHeight;
        input.style.height = Math.min(scrollHeight, 144) + 'px';
    },

    handleKeydown(e, onSend) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            onSend();
        }
    },

    clear() {
        const input = document.getElementById('chatInput');
        input.value = '';
        this.clearAttachments();
        this.setAttachmentHint('');
        this.autoResize();
    },

    focus() {
        const input = document.getElementById('chatInput');
        input.focus();
    },

    getValue() {
        const input = document.getElementById('chatInput');
        return input.value.trim();
    },

    getAttachments() {
        return window.NotionAI.Core.State.get('pendingAttachments') || [];
    },

    setAttachments(items) {
        window.NotionAI.Core.State.set('pendingAttachments', items);
        this.renderAttachments();
    },

    readFileAsDataUrl(file) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(typeof reader.result === 'string' ? reader.result : '');
            reader.onerror = () => reject(new Error('Failed to read image file.'));
            reader.readAsDataURL(file);
        });
    },

    loadImage(dataUrl) {
        return new Promise((resolve, reject) => {
            const img = new Image();
            img.onload = () => resolve(img);
            img.onerror = () => reject(new Error('Failed to load image.'));
            img.src = dataUrl;
        });
    },

    canvasToDataUrl(canvas, mimeType, quality) {
        return canvas.toDataURL(mimeType, quality);
    },

    async normalizeImageFile(file) {
        const originalDataUrl = await this.readFileAsDataUrl(file);
        if (file.size <= this.MAX_IMAGE_BYTES) {
            return {
                name: file.name || 'image',
                url: originalDataUrl,
                size: file.size,
                compressed: false
            };
        }

        const image = await this.loadImage(originalDataUrl);
        const longestEdge = Math.max(image.width, image.height) || 1;
        const scale = Math.min(1, this.MAX_EDGE / longestEdge);
        const width = Math.max(1, Math.round(image.width * scale));
        const height = Math.max(1, Math.round(image.height * scale));

        const canvas = document.createElement('canvas');
        canvas.width = width;
        canvas.height = height;
        const ctx = canvas.getContext('2d');
        if (!ctx) {
            return {
                name: file.name || 'image',
                url: originalDataUrl,
                size: file.size,
                compressed: false
            };
        }
        ctx.drawImage(image, 0, 0, width, height);
        const dataUrl = this.canvasToDataUrl(canvas, 'image/jpeg', this.JPEG_QUALITY);
        return {
            name: file.name || 'image',
            url: dataUrl,
            size: dataUrl.length,
            compressed: true
        };
    },

    async addFiles(fileList) {
        const current = this.getAttachments();
        const imageFiles = Array.from(fileList || []).filter(file => file && file.type && file.type.startsWith('image/'));
        const availableSlots = Math.max(0, this.MAX_ATTACHMENT_COUNT - current.length);
        const acceptedFiles = imageFiles.slice(0, availableSlots);

        if (imageFiles.length > availableSlots) {
            this.setAttachmentHint(`Only ${this.MAX_ATTACHMENT_COUNT} images can be attached per message.`);
        } else {
            this.setAttachmentHint('');
        }

        const additions = [];
        for (const file of acceptedFiles) {
            const normalized = await this.normalizeImageFile(file);
            additions.push({
                id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
                name: normalized.name,
                url: normalized.url,
                size: normalized.size,
                compressed: normalized.compressed
            });
        }

        if (additions.some(item => item.compressed)) {
            this.setAttachmentHint('Large images were compressed before sending.');
        }

        this.setAttachments([...current, ...additions]);
    },

    removeAttachment(id) {
        const remaining = this.getAttachments().filter(item => item.id !== id);
        this.setAttachments(remaining);
        if (!remaining.length) {
            this.setAttachmentHint('');
        }
    },

    clearAttachments() {
        window.NotionAI.Core.State.set('pendingAttachments', []);
        this.renderAttachments();
        const input = document.getElementById('imageUploadInput');
        if (input) {
            input.value = '';
        }
    },

    renderAttachments() {
        const container = document.getElementById('attachmentPreviewList');
        if (!container) {
            return;
        }
        const attachments = this.getAttachments();
        container.innerHTML = '';
        attachments.forEach(item => {
            const chip = document.createElement('div');
            chip.className = 'attachment-chip';
            const sizeKb = Math.max(1, Math.round((Number(item.size || 0) / 1024)));
            chip.innerHTML = `<span>${item.name}${item.compressed ? ' (optimized)' : ''} · ${sizeKb}KB</span><button type="button" data-attachment-id="${item.id}">x</button>`;
            chip.querySelector('button').addEventListener('click', () => {
                this.removeAttachment(item.id);
            });
            container.appendChild(chip);
        });
    },

    buildMessageContent() {
        const text = this.getValue();
        const attachments = this.getAttachments();
        const parts = [];

        if (text) {
            parts.push({ type: 'text', text });
        }

        attachments.forEach(item => {
            parts.push({
                type: 'image_url',
                image_url: { url: item.url }
            });
        });

        if (parts.length === 0) {
            return '';
        }
        if (parts.length === 1 && parts[0].type === 'text') {
            return parts[0].text;
        }
        return parts;
    },

    enable() {
        const input = document.getElementById('chatInput');
        const sendBtn = document.getElementById('sendBtn');
        const uploadBtn = document.getElementById('imageUploadBtn');
        input.disabled = false;
        sendBtn.disabled = false;
        if (uploadBtn) uploadBtn.disabled = false;
    },

    disable() {
        const input = document.getElementById('chatInput');
        const sendBtn = document.getElementById('sendBtn');
        const uploadBtn = document.getElementById('imageUploadBtn');
        input.disabled = true;
        sendBtn.disabled = true;
        if (uploadBtn) uploadBtn.disabled = true;
    }
};
