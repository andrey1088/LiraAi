import { LiraUtils } from './utils.js';
/**
 * VIEW: DOM rendering and updates
 */
export class LiraView {
    constructor() {
        const { $ } = LiraUtils;
        this.chatContainer = $('chat');
        this.lastLiraBubble = null;
        this.blockingLoader = null;
        this.imageCanvas = null;
    }

    createImageEditor() {
        document.getElementById('chat').innerHTML = '';
    }

    showBlockingLoader(text = 'Please wait…') {
        let overlay = document.getElementById('blocking-loader');
        if (!overlay) {
            overlay = LiraUtils.create('div', 'blocking-loader-root', `
            <div class="blocking-loader__box">
                <div class="blocking-loader__spinner"></div>
                <div class="blocking-loader__text">${text}</div>
            </div>`);
            overlay.id = 'blocking-loader';
            document.body.appendChild(overlay);
        } else {
            overlay.classList.add('blocking-loader-root');
            const el = overlay.querySelector('.blocking-loader__text');
            if (el) el.textContent = text;
        }
        overlay.classList.add('is-visible');
        overlay.setAttribute('aria-hidden', 'false');
        this.blockingLoader = overlay;
        void overlay.offsetHeight;
    }

    hideBlockingLoader() {
        const overlay = this.blockingLoader || document.getElementById('blocking-loader');
        if (!overlay) return;
        overlay.classList.remove('is-visible');
        overlay.setAttribute('aria-hidden', 'true');
        this.blockingLoader = null;
    }

    updateBlockingLoader(text) {
        if (!this.blockingLoader) {
            this.showBlockingLoader(text);
            return;
        }
        const el = this.blockingLoader.querySelector('.blocking-loader__text');
        if (el) el.textContent = text;
    }

    addMessage(role, text, icon, images = '', isStream = false) {
        if (!this.chatContainer) return;

        if (!isStream) {
            let bubbleContent = '';

            // Images arrived (string or array)
            if (images && images.length > 0) {
                const imgArray = Array.isArray(images) ? images : [images];

                // Multiple → grid; single → default class
                const containerClass = imgArray.length > 1 ? 'chat-images-grid' : 'chat-img-single';

                bubbleContent += `<div class="${containerClass}">`;
                imgArray.forEach(url => {
                    const click = role === 'user'
                        ? 'onclick="liraApp.showFullImageFromChat(this.src)"'
                        : `onclick="liraApp.showFullImage(this.src)"`;
                    bubbleContent += `<img src="${url}" class="chat-img" ${click}>`;
                });
                bubbleContent += `</div>`;
            }

            if (text) {
                bubbleContent += `<div class="bubble-text">${LiraUtils.escapeHTML(text)}</div>`;
            }

            const div = LiraUtils.create('div', `msg ${role === 'user' ? 'user' : 'model'}`, `
                <img src="${icon}" class="avatar">
                <div class="bubble">${bubbleContent}</div>
            `);

            this.chatContainer.appendChild(div);

            if (role !== 'user') {
                this.lastLiraBubble = div.querySelector('.bubble-text');
            }
        } else if (this.lastLiraBubble) {
            this.lastLiraBubble.textContent += text;
        }
        this.scrollToBottom();
    }

    renderImagePreview(base64, onClear) {
        let previewContainer = LiraUtils.$('image-preview-container');
        if (!previewContainer) {
            previewContainer = LiraUtils.create('div');
            previewContainer.id = 'image-preview-container';
            document.querySelector('.input-area').prepend(previewContainer);
        }

        previewContainer.innerHTML = `
            <div class="preview-item">
                <img src="${base64}" style="max-height: 80px; border-radius: 5px;">
                <div class="remove-preview" id="remove-img-btn" style="cursor:pointer;">×</div>
            </div>
        `;
        LiraUtils.$('remove-img-btn').onclick = onClear;
    }

    clearImagePreview() {
        const pc = LiraUtils.$('image-preview-container');
        if (pc) pc.innerHTML = '';
    }

    renderSystemMessage(text) {
        const div = LiraUtils.create('div', 'system-msg', `<span>— ${text} —</span>`);
        this.chatContainer.appendChild(div);
        this.scrollToBottom();
    }

    clearChat() {
        LiraUtils.clear(this.chatContainer);
    }

    scrollToBottom() {
        this.chatContainer.scrollTop = this.chatContainer.scrollHeight;
    }

    updateInputHeight() {
        const input = LiraUtils.$('userInput');
        input.style.height = 'auto';
        input.style.height = (input.scrollHeight) + 'px';
    }

    resetInput() {
        const input = LiraUtils.$('userInput');
        input.value = '';
        input.style.height = 'auto';
        input.focus();
    }

    renderImageCanvas(imageUrl = '', prompt = '') {
        // Create container if missing
        if (!this.imageCanvas) {
            this.chatContainer.innerHTML = ''; // Clear chat entirely
            this.imageCanvas = LiraUtils.create('div', 'image-canvas-container');
            this.chatContainer.appendChild(this.imageCanvas);
        }

        // Update content when image arrives
        if (imageUrl) {
            this.imageCanvas.innerHTML = `
                <div class="current-gen-wrapper">
                    <img src="${imageUrl}" class="main-canvas-img" onclick="window.open(this.src)">
                    <div class="gen-info-overlay">${prompt}</div>
                </div>
            `;
        } else {
            this.imageCanvas.innerHTML = `
                <div class="canvas-placeholder">
                    <span>Ready to generate…</span>
                </div>
            `;
        }
    }

    showChatLoader(text) {
        const chat = document.getElementById('chat');
        if (chat) {
            chat.innerHTML = `
                <div class="chat-local-loader">
                    <div class="spinner"></div>
                    <p>${text}</p>
                </div>
            `;
        }
    }

    hideChatLoader() {
        const loader = document.querySelector('.chat-local-loader');
        if (loader) loader.remove();
    }

    /** Chat footer until first model token (text from backend). */
    showThinkingIndicator(text = 'Thinking…') {
        if (!this.chatContainer) return;
        this.hideThinkingIndicator();
        const safe = LiraUtils.escapeHTML(text || 'Thinking…');
        const row = LiraUtils.create('div', 'thinking-indicator', `
            <div class="thinking-indicator__spinner"></div>
            <span class="thinking-indicator__text">${safe}</span>
        `);
        row.id = 'thinking-indicator';
        this.chatContainer.appendChild(row);
        this.scrollToBottom();
    }

    hideThinkingIndicator() {
        document.getElementById('thinking-indicator')?.remove();
    }
}