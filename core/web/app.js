import {LiraUtils} from './utils.js';
import {LiraState} from './state.js';
import {LiraView} from './view.js';
import {I18n} from './i18n.js';
import {
    MAX_ATTACHMENT_BYTES,
    arrayBufferToBase64,
    releaseAllPendingAttachments,
    revokeAttachmentPreview,
} from './attachments.js';

const LIMBIC_EMOTIONS = new Set([
    'neutral',
    'happiness',
    'sadness',
    'enthusiasm',
    'fear',
    'anger',
    'disgust',
]);

export class LiraApp {
    constructor() {
        this.backend = null;
        this._galleryDescribeHintModelId = null;
        this._galleryMissingDescriptionCount = 0;
        this._sidebarTasksRenderGen = 0;
        this.state = new LiraState();
        this.view = new LiraView();
        this.i18n = new I18n();
        this.view.hideBlockingLoader();
        this.initBridge();

        document.addEventListener('keydown', (e) => {
            const overlay = document.getElementById('fullImageOverlay');
            // Check whether overlay is open
            if (overlay && overlay.style.display !== 'none') {
                if (e.key === 'ArrowRight') {
                    liraApp.nextImage();
                } else if (e.key === 'ArrowLeft') {
                    liraApp.prevImage();
                } else if (e.key === 'Escape') {
                    liraApp.closeFullImage();
                }
            }
        });
    }

    initBridge() {
        new QWebChannel(qt.webChannelTransport, (channel) => {
            this.backend = channel.objects.backend;
            this.backend.on_web_ready();
            this.syncState();
        });
    }

    /** Let WebEngine paint overlay before blocking Python call. */
    _yieldToPaint() {
        return new Promise((resolve) => {
            requestAnimationFrame(() => requestAnimationFrame(resolve));
        });
    }

    t(key, vars) {
        return this.i18n ? this.i18n.t(key, vars) : key;
    }

    async loadUiLocale() {
        if (!this.backend?.get_ui_locale) {
            await this.i18n.load('ru');
            return;
        }
        const raw = await this.backend.get_ui_locale();
        const { locale } = JSON.parse(raw);
        await this.i18n.load(locale || 'ru');
        this.i18n.applyDom();
    }

    async onLocaleChange(locale) {
        if (!this.backend?.set_ui_locale) return;
        await this._yieldToPaint();
        const raw = await this.backend.set_ui_locale(locale);
        const { locale: saved } = JSON.parse(raw);
        await this.i18n.load(saved || locale);
        this.i18n.applyDom();
        await this.updateUI();
    }

    async syncState() {
        await this.loadUiLocale();
        const data = await this.backend.get_active_model_info();
        this.state.modelInfo = { ...(this.state.modelInfo || {}), ...JSON.parse(data) };
        const mc = this.state.modelInfo.model_class;
        this.state.isImageEdit = (mc === 'image-edit');
        this.state.isImageGenerator = (mc === 'text-to-image' || mc === 'image-edit');

        if (this.state.isImageGenerator) {
            console.log(`🎨 ${this.t('Artist mode active')}`);
            this.state.currentSessionId = null;
        } else {
            this.state.currentSessionId = this.state.modelInfo.current_session_id;
        }
        if (!this.state.isImageEdit) {
            this.state.imageEditPrimaryB64 = null;
            this.state.imageEditSecondaryB64 = null;
        }
        await this.updateUI();
        await this.refreshLimbicUI();
        this.syncGalleryToolsPanel();
    }

    applyLimbicLayout() {
        const inner = document.getElementById('chat-inner');
        const aside = document.getElementById('limbic-aside');
        const img = document.getElementById('limbic-emotion-img');
        const toolsPanel = document.getElementById('limbic-tools-panel');
        if (!inner || !aside) return;
        const hasLimbic = !this.state.isImageGenerator && !!this.state.limbicImagesBase;
        const hasTools = LiraState.modelSupportsGalleryDescribe(this.state.modelInfo);
        const show = hasLimbic || hasTools;
        inner.classList.toggle('chat-inner--limbic', show);
        aside.setAttribute('aria-hidden', show ? 'false' : 'true');
        aside.classList.toggle('limbic-aside--tools-only', hasTools && !hasLimbic);
        if (toolsPanel) {
            toolsPanel.hidden = !hasTools;
        }
        if (img) {
            img.hidden = !hasLimbic;
            if (!hasLimbic) {
                img.removeAttribute('src');
            }
        }
    }

    setLimbicEmotion(payload) {
        const emotionKey = typeof payload === 'string' ? payload : payload?.emotion;
        const baseUrl = typeof payload === 'object' && payload?.baseUrl
            ? payload.baseUrl
            : this.state.limbicImagesBase;
        if (!baseUrl) {
            this.state.limbicImagesBase = null;
            this.syncGalleryToolsPanel();
            return;
        }
        this.state.limbicImagesBase = baseUrl;
        const key = LIMBIC_EMOTIONS.has(emotionKey) ? emotionKey : 'neutral';
        const img = document.getElementById('limbic-emotion-img');
        if (!img) return;
        img.src = `${baseUrl}${key}.png`;
        img.alt = '';
        this.syncGalleryToolsPanel();
        if (this.state.modelInfo) {
            this.state.modelInfo.limbic_dominant_emotion = key;
            this.state.modelInfo.limbic_images_base_url = baseUrl;
        }
    }

    async refreshLimbicUI() {
        if (this.state.isImageGenerator || !this.backend) {
            this.state.limbicImagesBase = null;
            this.syncGalleryToolsPanel();
            return;
        }
        try {
            const data = await this.backend.get_active_model_info();
            const info = JSON.parse(data);
            this.state.limbicImagesBase = info.limbic_images_base_url || null;
            if (this.state.limbicImagesBase && info.limbic_dominant_emotion) {
                this.setLimbicEmotion({
                    emotion: info.limbic_dominant_emotion,
                    baseUrl: this.state.limbicImagesBase,
                });
            } else {
                this.syncGalleryToolsPanel();
            }
        } catch (e) {
            console.warn('[Limbic] refresh failed', e);
            this.state.limbicImagesBase = null;
            this.syncGalleryToolsPanel();
        }
    }

    bindUserInputActivity(inputEl) {
        if (!inputEl || inputEl._liraActivityBound) return;
        inputEl._liraActivityBound = true;
        if (!this.backend?.set_user_typing) return;
        let typingTimer = null;
        const notify = (active) => {
            try {
                this.backend.set_user_typing(active);
            } catch (e) {
                console.warn('[Activity] set_user_typing failed', e);
            }
        };
        inputEl.addEventListener('input', () => {
            notify(true);
            clearTimeout(typingTimer);
            typingTimer = setTimeout(() => notify(false), 1500);
        });
        inputEl.addEventListener('blur', () => {
            clearTimeout(typingTimer);
            notify(false);
        });
    }

    renderInputPanel() {
        const container = LiraUtils.$('input-container-wrapper');
        if (!container) return;

        if (this.state.isImageEdit) {
            container.innerHTML = `
                <div class="input-area image-gen-mode">
                    <div id="image-preview-container" class="preview-bar preview-bar--image-edit"></div>
                    <textarea id="userInput" placeholder="${this.t('What should we do with the image(s)? For two photos describe the composition (English is usually more stable)…')}"></textarea>
                    <input
                            type="file"
                            id="file-input-edit-primary"
                            style="display:none"
                            accept="image/jpeg, image/png, image/webp"
                            onchange="liraApp.handleImageEditPrimary(this)">
                    <input
                            type="file"
                            id="file-input-edit-secondary"
                            style="display:none"
                            accept="image/jpeg, image/png, image/webp"
                            onchange="liraApp.handleImageEditSecondary(this)">
                    <div class="gen-controls">
                        <button id="attach-btn" type="button" class="paint-btn" title="${this.t('Main image (required)')}" onclick="document.getElementById('file-input-edit-primary').click()">${this.t('Photo')}</button>
                        <button id="attach-btn-2" type="button" class="paint-btn paint-btn--secondary" title="${this.t('Second image for composition (optional)')}" onclick="document.getElementById('file-input-edit-secondary').click()">${this.t('Photo 2')}</button>
                        <button id="sendBtn" class="paint-btn" onclick="liraApp.handleSend()">${this.t('Apply edit')}</button>
                    </div>
                </div>
            `;
        } else if (this.state.modelInfo.model_class === 'text-to-image') {
            const settings = this.state.modelInfo.settings || {};
            const pos = settings.positive_prefix || "";
            const neg = settings.negative_prompt || "";

            container.innerHTML = `
                <div class="input-area image-gen-mode">
                    <textarea id="userInput" placeholder="${this.t('What to draw? (prompt)…')}">${pos}</textarea>
                    <textarea id="negativeInput" placeholder="${this.t('Exclude (negative prompt)…')}">${neg}</textarea>
                    <div class="gen-controls">
                        <select id="ratioSelect">
                            <option value="1:1" ${settings.ratio === '1:1' ? 'selected' : ''}>${this.t('Square 1:1')}</option>
                            <option value="16:9" ${settings.ratio === '16:9' ? 'selected' : ''}>${this.t('Landscape 16:9')}</option>
                            <option value="9:16" ${settings.ratio === '9:16' ? 'selected' : ''}>${this.t('Portrait 9:16')}</option>
                        </select>
                        <button id="loraBtn" class="paint-btn lora-btn" onclick="liraApp.openLoraModal()" title="${this.t('Apply LoRA')}">🎨 LoRA</button>
                        <button id="sendBtn" class="paint-btn" onclick="liraApp.handleSend()">${this.t('Draw')}</button>
                    </div>
                </div>
            `;
        } else {
            container.innerHTML = `
                <div class="input-area">
                    <div id="image-preview-container" class="preview-bar"></div>
                    
                    <label for="userInput"></label>
                    <textarea id="userInput" placeholder="${this.t('Message…')}"></textarea>
                    <input
                            type="file"
                            id="file-input"
                            style="display:none"
                            accept="image/jpeg,image/png,image/webp,.pdf,.txt,.md,.csv,application/pdf,text/plain"
                            multiple
                            onchange="liraApp.handleFile(this)">
                    <div class="action-buttons">
                        <button id="sendBtn" onclick="liraApp.handleSend()">➤</button>
                        <div class="additional-actions">
                            <button id="saveBtn" type="button" title="${this.t('Save to dataset')}" onclick="liraApp.handleSave()">+</button>
                            <button id="memoryVerifiedBtn" type="button" title="${this.t('Save to memory')}" onclick="liraApp.handleMarkMemory()">🧠</button>
                            <button id="attach-btn" type="button" title="${this.t('Photo')}" onclick="document.getElementById('file-input').click()">📎</button>
                            <button id="camera-attach-btn" type="button" title="${this.t('Take a photo with the camera')}" onclick="liraApp.handleCameraAttach()">📷</button>
                        </div>
                    </div>
                </div>
            `;

            const inputArea = document.getElementById('userInput');
            inputArea.addEventListener('dragover', (e) => e.preventDefault());
            inputArea.addEventListener('drop', async (e) => {
                e.preventDefault();
                const url = e.dataTransfer.getData('text/uri-list') || e.dataTransfer.getData('text/plain');
                if (!url) return;
                const resp = await fetch(url);
                const blob = await resp.blob();
                const file = new File([blob], "image.jpg", {type: blob.type});
                const dataTransfer = new DataTransfer();
                dataTransfer.items.add(file);
                const fileInput = document.getElementById('file-input');
                fileInput.files = dataTransfer.files;
                liraApp.handleFile(fileInput);
            });
        }

        // Attachment previews: chat and Qwen Image Edit; SD artist has none
        if (!this.state.isImageGenerator || this.state.isImageEdit) {
            this.renderMultiImagePreview();
        }

        this.bindUserInputActivity(document.getElementById('userInput'));
        this.initEvents();
        this.syncGalleryToolsPanel();
    }

    syncGalleryToolsPanel() {
        this.applyLimbicLayout();
        this.closeLimbicToolsMenu();
        void this.renderSidebarModelTasks().then(() => this.refreshGalleryDescribeBadges());
    }

    closeLimbicToolsMenu() {
        const menu = document.getElementById('limbic-tools-menu');
        const toggle = document.getElementById('limbic-tools-toggle');
        if (!menu || !toggle) return;
        menu.classList.remove('limbic-tools-menu--open');
        toggle.classList.remove('limbic-tools-toggle--open');
        const chevron = toggle.querySelector('.limbic-tools-toggle-chevron');
        if (chevron) {
            chevron.textContent = '▼';
        }
    }

    async renderSidebarModelTasks() {
        const menu = document.getElementById('limbic-tools-menu');
        if (!menu) return;
        const gen = ++this._sidebarTasksRenderGen;
        menu.replaceChildren();
        if (!LiraState.modelSupportsGalleryDescribe(this.state.modelInfo)) {
            return;
        }
        if (!this.backend?.get_sidebar_model_tasks) {
            return;
        }
        let tasks = [];
        try {
            const raw = await this.backend.get_sidebar_model_tasks();
            if (gen !== this._sidebarTasksRenderGen) {
                return;
            }
            tasks = typeof raw === 'string' ? JSON.parse(raw) : raw;
        } catch (e) {
            console.warn('[sidebar] tasks load failed', e);
            return;
        }
        if (gen !== this._sidebarTasksRenderGen) {
            return;
        }
        if (!Array.isArray(tasks)) {
            return;
        }
        menu.replaceChildren();
        for (const task of tasks) {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'limbic-tool-btn';
            btn.title = task.description || '';
            btn.dataset.taskId = task.id;
            if (task.attention_when_missing) {
                btn.dataset.attentionWhenMissing = '1';
            }
            btn.addEventListener('click', () => this.runSidebarModelTask(task.id));
            const text = document.createElement('span');
            text.className = 'limbic-tool-btn-text';
            text.textContent = task.label || task.id;
            btn.appendChild(text);
            if (task.badge === 'missing_count') {
                const badge = document.createElement('span');
                badge.id = 'gallery-describe-missing-badge';
                badge.className = 'limbic-badge limbic-badge--count';
                badge.hidden = true;
                badge.setAttribute('aria-hidden', 'true');
                btn.appendChild(badge);
            }
            menu.appendChild(btn);
        }
        if (gen !== this._sidebarTasksRenderGen) {
            menu.replaceChildren();
        }
    }

    async runSidebarModelTask(taskId) {
        if (!this.backend?.run_sidebar_model_task) {
            this.showToast(this.t('Task unavailable'));
            return;
        }
        const isGalleryDescribe = String(taskId || '').startsWith('gallery_describe');
        if (isGalleryDescribe) {
            this.openGalleryDescribeModal(null);
            await this._yieldToPaint();
        }
        try {
            const raw = await this.backend.run_sidebar_model_task(taskId);
            const res = JSON.parse(raw);
            if (isGalleryDescribe && !res.ok && !res.accepted) {
                this.closeGalleryDescribeModal();
                this.showToast(res.error || this.t('Failed to start'));
            }
        } catch (e) {
            console.error(e);
            if (isGalleryDescribe) {
                this.closeGalleryDescribeModal();
            }
            this.showToast(this.t('Start error'));
        }
    }

    toggleLimbicToolsMenu() {
        const menu = document.getElementById('limbic-tools-menu');
        const toggle = document.getElementById('limbic-tools-toggle');
        if (!menu || !toggle) return;
        const open = menu.classList.toggle('limbic-tools-menu--open');
        toggle.classList.toggle('limbic-tools-toggle--open', open);
        const chevron = toggle.querySelector('.limbic-tools-toggle-chevron');
        if (chevron) {
            chevron.textContent = open ? '▲' : '▼';
        }
    }

    setGalleryInputCompact(compact) {
        const wrapper = document.getElementById('input-container-wrapper');
        if (!wrapper) return;
        wrapper.style.display = compact ? 'none' : 'block';
    }

    renderModelList(models, activeId) {
        const modelList = LiraUtils.$('model-list');
        const activeModelSelector = LiraUtils.$('active-model');
        const modelSwitcherSelector = LiraUtils.$('model-switcher');
        if (!modelList || !activeModelSelector) return;

        LiraUtils.clear(modelList);
        LiraUtils.clear(activeModelSelector);

        models.forEach(model => {
            const item = LiraUtils.create('div', `model-item ${model.id === activeId ? 'active' : ''}`, `
                <div class="model-name">${model.name}</div>
                <img src="${model.icon_base64}" class="mini-avatar">`);

            if (model.id === activeId) {
                activeModelSelector.appendChild(item);
                item.onclick = () => modelSwitcherSelector.classList.toggle('opened');
            } else {
                item.onclick = () => {
                    modelSwitcherSelector.classList.remove('opened');
                    void this._yieldToPaint().then(() => {
                        this.backend.switch_model(model.id);
                    });
                };
                modelList.appendChild(item);
            }
        });
    }

    renderSidebar(models, activeId) {
        const current = models.find(m => String(m.id) === String(activeId));
        if (current) {
            this.renderModelInfo(current);
            this.renderModelSettings(current);
            this.renderHistoryList();
        }
    }

    renderModelInfo(model) {
        const el = LiraUtils.$('sidebar-model-info');
        el.innerHTML = `
            <img src="${model.icon_base64}" class="mini-avatar">
            <div class="model-name">${model.name}</div>`;
    }

    renderModelSettings(model) {
        const el = LiraUtils.$('sidebar-settings-list');
        const isImageGen = model.model_class === 'text-to-image';
        const isImageEdit = model.model_class === 'image-edit';
        const settings = model.settings || {};

        let specificSettings = '';

        if (isImageEdit) {
            const pl = settings.placement || 'model_offload';
            const dt = settings.dtype || 'bfloat16';
            specificSettings = `
                <fieldset>
                    <label>Steps: <span id="val-steps">${settings.steps || 8}</span></label>
                    <input type="range" name="steps" min="1" max="50" step="1" value="${settings.steps || 8}"
                           oninput="liraApp.updateParam(this.value, 'val-steps')">
                </fieldset>
                <fieldset>
                    <label>true_cfg_scale: <span id="val-tcf">${settings.true_cfg_scale ?? 4}</span></label>
                    <input type="range" name="true_cfg_scale" min="1" max="8" step="0.5" value="${settings.true_cfg_scale ?? 4}"
                           oninput="liraApp.updateParam(this.value, 'val-tcf')">
                </fieldset>
                <fieldset>
                    <label>guidance_scale: <span id="val-gs">${settings.guidance_scale ?? 1}</span></label>
                    <input type="range" name="guidance_scale" min="1" max="5" step="0.5" value="${settings.guidance_scale ?? 1}"
                           oninput="liraApp.updateParam(this.value, 'val-gs')">
                </fieldset>
                <fieldset>
                    <label>placement</label>
                    <select name="placement">
                        <option value="model_offload" ${pl === 'model_offload' ? 'selected' : ''}>model_offload</option>
                        <option value="full_gpu" ${pl === 'full_gpu' ? 'selected' : ''}>full_gpu</option>
                    </select>
                </fieldset>
                <fieldset>
                    <label>dtype</label>
                    <select name="dtype">
                        <option value="bfloat16" ${dt === 'bfloat16' ? 'selected' : ''}>bfloat16</option>
                        <option value="float16" ${dt === 'float16' ? 'selected' : ''}>float16</option>
                    </select>
                </fieldset>
                <fieldset>
                    <label><input type="checkbox" name="text_encoder_gpu" value="true" ${settings.text_encoder_gpu ? 'checked' : ''}> ${this.t('text_encoder on GPU (OOM risk)')}</label>
                </fieldset>
                <fieldset class="prompt-fieldset">
                    <label>hf_repo_id</label>
                    <input type="text" name="hf_repo_id" class="sd-input-field" value="${settings.hf_repo_id || 'Qwen/Qwen-Image-Edit-2511'}">
                </fieldset>
            `;
        } else if (isImageGen) {
            // Artist-only settings: Steps, CFG, prompts
            specificSettings = `
                <fieldset>
                    <label>Steps: <span id="val-steps">${settings.steps || 30}</span></label>
                    <input type="range" name="steps" min="1" max="50" step="1" value="${settings.steps || 30}" 
                           oninput="liraApp.updateParam(this.value, 'val-steps')">
                </fieldset>
                <fieldset>
                    <label>CFG Scale: <span id="val-cfg">${settings.cfg_scale || 7}</span></label>
                    <input type="range" name="cfg_scale" min="1" max="20" step="0.5" value="${settings.cfg_scale || 7}" 
                           oninput="liraApp.updateParam(this.value, 'val-cfg')">
                </fieldset>
                <fieldset class="prompt-fieldset">
                    <label>Positive Prefix</label>
                    <textarea name="positive_prefix" class="sd-input-field" style="height: 100px;">${settings.positive_prefix || ''}</textarea>
                </fieldset>
                <fieldset class="prompt-fieldset">
                    <label>Negative Prompt</label>
                    <textarea name="negative_prompt" class="sd-input-field" style="height: 100px;">${settings.negative_prompt || ''}</textarea>
                </fieldset>
            `;
        } else {
            const vol = settings.volume !== undefined ? settings.volume : 0.8;
            const ngl = settings.n_gpu_layers !== undefined ? settings.n_gpu_layers : -1;
            const nCtx = settings.n_ctx || 8192;
            const maxTok = settings.max_tokens || 2048;
            const reserve = settings.context_reserve_tokens || 2176;
            const tmplSlack = settings.context_template_slack_tokens || 384;
            const hwCap = Math.max(1800, nCtx - Math.max(reserve, maxTok + 128) - tmplSlack);
            const budget = settings.context_budget_tokens || hwCap;
            specificSettings = `
                <fieldset>
                    <div class="setting-label-with-hint">
                        <label>Temperature: <span id="val-temp">${settings.temperature || 0.7}</span></label>
                        <input type="range" name="temperature" min="0" max="1.5" step="0.1" value="${settings.temperature || 0.7}" 
                            oninput="liraApp.updateParam(this.value, 'val-temp')">
                    </div>
                </fieldset>
                <fieldset>
                    <div class="setting-label-with-hint">
                        <label>Top P: <span id="val-top_p">${settings.top_p || 0.9}</span></label>
                        <input type="range" name="top_p" min="0" max="1" step="0.1" value="${settings.top_p || 0.9}" 
                               oninput="liraApp.updateParam(this.value, 'val-top_p')">
                    </div>
                </fieldset>
                <fieldset>
                    <div class="setting-label-with-hint">
                        <label>${this.t('GPU layers (n_gpu_layers):')} <span id="val-ngl">${ngl}</span></label>
                        <input type="range" name="n_gpu_layers" min="-1" max="64" step="1" value="${ngl}"
                               oninput="liraApp.updateParam(this.value, 'val-ngl')">
                    </div>
                    <p style="font-size:11px;opacity:0.65;margin:4px 0 0;">${this.t('−1 = all layers on GPU. Fewer layers use CPU, less VRAM, slower. Reload model after change.')}</p>
                </fieldset>
                <fieldset>
                    <div class="setting-label-with-hint">
                        <label>${this.t('History budget (tokens):')} <span id="val-ctx-budget">${budget}</span></label>
                        <input type="range" name="context_budget_tokens" min="1800" max="${hwCap}" step="64" value="${Math.min(budget, hwCap)}"
                               oninput="liraApp.updateParam(this.value, 'val-ctx-budget')">
                    </div>
                    <p style="font-size:11px;opacity:0.65;margin:4px 0 0;">${this.t('Hardware cap at n_ctx={nCtx}: ~{hwCap} (reserve for reply + template).', { nCtx, hwCap })}</p>
                </fieldset>
                <fieldset>
                    <div class="setting-label-with-hint">
                        <label>${this.t('Volume:')} <span id="val-volume">${Math.round(vol * 100)}%</span></label>
                        <input type="range" name="volume" min="0" max="1" step="0.1" value="${vol}" 
                               oninput="liraApp.updateVolume(this.value)">
                    </div>
                </fieldset>
            `;
        }

        el.innerHTML = `
            <button onclick="liraApp.closeSubMenu()" class="back-btn">${this.t('Back')}</button>
            <div class="model-information">
                <div class="logo-container"><img src="${model.icon_base64}" class="model-avatar"></div>
                <div class="model-description">
                    <p><b>${model.name}</b></p>
                    <p style="font-size: 11px; opacity: 0.6;">${model.model_type}</p>
                </div>
            </div>
            <div class="settings-group">
                <form id="model-settings-form">
                    ${specificSettings}
                    <button class="action primary" type="button" onclick="liraApp.submitModelParams()">${this.t('Save')}</button>
                </form>
            </div>`;
    }

    async renderHistoryList() {
        if (this.state.isImageGenerator) {
            return;
        }

        const el = LiraUtils.$('sidebar-history-list');
        if (!el) return;
        const data = await this.backend.get_chat_history_list();
        const sessions = JSON.parse(data);

        el.innerHTML = `<button onclick="liraApp.closeSubMenu()" class="back-btn">${this.t('Back')}</button>`;
        const chatsList = LiraUtils.create('ul', 'history-list');
        sessions.forEach(session => {
            const isActive = session.id === this.state.currentSessionId;
            const item = LiraUtils.create('li', `history-item ${isActive ? 'active' : ''}`, `
                <div class="history-info" onclick="${isActive ? '' : `liraApp.loadChatSession(${session.id})`}">
                    <div class="history-title">${session.title}</div>
                    <div class="history-date">${session.date}</div>
                </div>
                <div class="history-actions">
                    <button onclick="liraApp.editChatPrompt(${session.id}, '${session.title}')">✏️</button>
                    <button onclick="liraApp.deleteChatPrompt(${session.id})">🗑️</button>
                </div>`);
            chatsList.appendChild(item);
        });
        el.appendChild(chatsList);
    }

    async loadChatSession(sessionId) {
        this._leaveSearchIfNeeded();
        const data = await this.backend.load_session(sessionId);
        const payload = JSON.parse(data);

        if (payload.pending) {
            this.view.showBlockingLoader(this.t('Please wait…'));
            this.state.currentSessionId = payload.session_id;
            this.closeSidebar();
            return;
        }

        this.state.currentSessionId = payload.session_id || sessionId;
        this.view.clearChat();

        (payload.messages || []).forEach(msg => {
            // Use the correct icon
            const icon = msg.role === 'user' ? this.state.modelInfo.user_icon : this.state.modelInfo.icon;

            // Gallery handling (unchanged)
            if (msg.role === 'assistant' && msg.content.startsWith('UI_GALLERY|')) {
                try {
                    const images = JSON.parse(msg.content.replace('UI_GALLERY|', ''));
                    this.renderGallerySearch(images);
                    return;
                } catch (e) {
                    console.error(this.t('Gallery restore error:'), e);
                }
            }

            // KEY CHANGE:
            // Pass msg.image_url — array or string in new logic.
            // view.addMessage handles rendering.
            this.view.addMessage(
                msg.role,
                msg.content,
                icon,
                msg.image_url || '', // May be an array ['img1', 'img2']
                false
            );
        });

        this.renderHistoryList();
        this.closeSidebar();
        this.view.hideBlockingLoader();
    }

    showImagePreview(base64) {
        this.view.renderImagePreview(base64, () => this.clearImage());
    }

    clearImage() {
        this.view.clearImagePreview();
        if (this.backend) this.backend.onImageSelected("");
    }

    showThinkingIndicator(text) {
        if (this.view) this.view.showThinkingIndicator(text);
    }

    hideThinkingIndicator() {
        if (this.view) this.view.hideThinkingIndicator();
    }

    addMessageFromJson(data) {
        // Check new images field or legacy image_url
        // Array or string — view.addMessage accepts both
        const images = data.images || data.image_url || [];
        const role = data.role === 'lira' ? 'model' : data.role;

        this.view.addMessage(
            role,
            data.text,
            data.icon,
            images,
            data.isStream || false
        );
    }

    // Fix argument forwarding (stray '=' bug)
    addMessage(role, text, icon, image_url = '', isStream = false) {
        this.view.addMessage(role, text, icon, image_url, isStream);
        // Clear previews on user message
        if (role === 'user') this.clearImage();
    }

    clearPendingAttachmentsOnModelSwitch() {
        releaseAllPendingAttachments(this.state.pendingAttachments);
        this.state.pendingAttachments = [];
        if (this.backend?.clear_pending_attachments) {
            this.backend.clear_pending_attachments();
        }
        this.renderMultiImagePreview();
    }

    _parseAttachmentRegisterResponse(raw) {
        if (!raw) return null;
        try {
            const data = JSON.parse(raw);
            if (data.error) {
                alert(data.error);
                return null;
            }
            if (data.warning) {
                alert(data.warning);
            }
            return data;
        } catch {
            if (raw.startsWith('data:image')) {
                return { id: `legacy_${Date.now()}`, kind: 'image', preview: raw };
            }
            return null;
        }
    }

    _pushPendingAttachment(data) {
        if (!data?.id) return;
        this.state.pendingAttachments.push({
            id: data.id,
            kind: data.kind || 'image',
            name: data.name || '',
            previewUrl: data.preview || '',
        });
        this.renderMultiImagePreview();
    }

    async _syncPendingAttachmentsFromBackend() {
        if (!this.backend?.get_pending_attachments_preview) {
            return;
        }
        try {
            const raw = await this.backend.get_pending_attachments_preview();
            const list = JSON.parse(raw || '[]');
            if (!Array.isArray(list) || list.length === 0) {
                return;
            }
            releaseAllPendingAttachments(this.state.pendingAttachments);
            for (const item of list) {
                this.state.pendingAttachments.push({
                    id: item.id,
                    kind: item.kind || 'image',
                    name: item.name || '',
                    previewUrl: item.preview || '',
                });
            }
            this.renderMultiImagePreview();
        } catch (e) {
            console.error('sync pending attachments:', e);
        }
    }

    async handleCameraAttach() {
        if (!this.backend || typeof this.backend.open_camera_for_attachment !== 'function') {
            return;
        }
        if (this.state.isImageGenerator && !this.state.isImageEdit) {
            return;
        }
        try {
            const sid = this.state.currentSessionId != null ? Number(this.state.currentSessionId) : 0;
            const raw = await this.backend.open_camera_for_attachment(sid);
            const data = this._parseAttachmentRegisterResponse(raw);
            if (data) {
                this._pushPendingAttachment(data);
            }
        } catch (e) {
            console.error('camera attach:', e);
        }
    }

    _isImageFile(file) {
        const t = (file.type || '').toLowerCase();
        return t === 'image/jpeg' || t === 'image/png' || t === 'image/webp'
            || /\.(jpe?g|png|webp)$/i.test(file.name || '');
    }

    _isDocumentFile(file) {
        const t = (file.type || '').toLowerCase();
        const n = (file.name || '').toLowerCase();
        if (t === 'application/pdf' || t === 'text/plain') return true;
        return /\.(pdf|txt|md|markdown|csv|log|json|xml|html?)$/i.test(n);
    }

    async _registerImageFile(file) {
        if (file.size > MAX_ATTACHMENT_BYTES) {
            alert(this.t('File {name} is too large. Max 6 MB.', { name: file.name }));
            return;
        }
        const base64Data = await new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = (e) => resolve(e.target.result);
            reader.onerror = reject;
            reader.readAsDataURL(file);
        });
        if (!this.backend?.register_image_attachment) {
            return;
        }
        const raw = await this.backend.register_image_attachment(base64Data);
        const data = this._parseAttachmentRegisterResponse(raw);
        if (data) {
            this._pushPendingAttachment(data);
        }
    }

    async _registerDocumentFileChunked(file) {
        const uploadId = await this.backend.begin_document_upload(file.name, file.size);
        if (!uploadId) {
            alert(this.t('Could not start document upload.'));
            return;
        }
        const chunkBytes = 192 * 1024;
        for (let offset = 0; offset < file.size; offset += chunkBytes) {
            const slice = file.slice(offset, Math.min(offset + chunkBytes, file.size));
            const ab = await slice.arrayBuffer();
            await this.backend.document_upload_chunk(uploadId, arrayBufferToBase64(ab));
        }
        const raw = await this.backend.finish_document_upload(uploadId);
        const data = this._parseAttachmentRegisterResponse(raw);
        if (data) {
            this._pushPendingAttachment(data);
            await this._syncPendingAttachmentsFromBackend();
        }
    }

    async _registerDocumentFile(file) {
        if (file.size > MAX_ATTACHMENT_BYTES) {
            alert(this.t('File {name} is too large. Max 6 MB.', { name: file.name }));
            return;
        }
        const name = file.name || 'document';
        const useChunked = file.size > 256 * 1024 || /\.pdf$/i.test(name);
        if (useChunked && this.backend?.begin_document_upload) {
            await this._registerDocumentFileChunked(file);
            return;
        }
        const dataUrl = await new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = (e) => resolve(e.target.result);
            reader.onerror = reject;
            reader.readAsDataURL(file);
        });
        const b64 = dataUrl.includes(',') ? dataUrl.split(',', 2)[1] : dataUrl;
        if (!this.backend?.register_document_attachment) {
            alert(this.t('Documents are not supported in this build.'));
            return;
        }
        const raw = await this.backend.register_document_attachment(file.name, b64);
        const data = this._parseAttachmentRegisterResponse(raw);
        if (data) {
            await this._syncPendingAttachmentsFromBackend();
        }
    }

    async handleFile(input) {
        const files = Array.from(input.files);
        if (files.length === 0) return;

        for (const file of files) {
            if (this._isImageFile(file)) {
                await this._registerImageFile(file);
            } else if (this._isDocumentFile(file)) {
                await this._registerDocumentFile(file);
            } else {
                alert(this.t('File {name}: only JPG, PNG, WebP or TXT/MD/PDF.', { name: file.name }));
            }
        }
        input.value = '';
    }

    _readOneImageFile(file, onDataUrl) {
        const validTypes = ['image/jpeg', 'image/png', 'image/webp'];
        if (!validTypes.includes(file.type)) {
            alert(this.t('File {name} is not supported. Only JPG, PNG and WebP.', { name: file.name }));
            return;
        }
        if (file.size > MAX_ATTACHMENT_BYTES) {
            alert(this.t('File {name} is too large. Max 6 MB.', { name: file.name }));
            return;
        }
        const reader = new FileReader();
        reader.onload = (e) => onDataUrl(e.target.result);
        reader.readAsDataURL(file);
    }

    handleImageEditPrimary(input) {
        const file = input.files && input.files[0];
        if (!file) return;
        this._readOneImageFile(file, (base64Data) => {
            this.state.imageEditPrimaryB64 = base64Data;
            this.renderMultiImagePreview();
            if (this.backend) this.backend.onImageEditPrimarySelected(base64Data);
        });
        input.value = '';
    }

    handleImageEditSecondary(input) {
        const file = input.files && input.files[0];
        if (!file) return;
        this._readOneImageFile(file, (base64Data) => {
            this.state.imageEditSecondaryB64 = base64Data;
            this.renderMultiImagePreview();
            if (this.backend) this.backend.onImageEditSecondarySelected(base64Data);
        });
        input.value = '';
    }

    renderMultiImagePreview() {
        const container = document.getElementById('image-preview-container');
        if (!container) return;

        if (this.state.isImageEdit) {
            const p = this.state.imageEditPrimaryB64;
            const s = this.state.imageEditSecondaryB64;
            if (!p && !s) {
                container.style.display = 'none';
                container.innerHTML = '';
                return;
            }
            container.style.display = 'flex';
            let html = '';
            if (p) {
                html += `<div class="preview-item preview-item--edit"><span class="preview-slot-label">1</span><img src="${p}" alt=""><div class="remove-btn" onclick="liraApp.removeImageEditSlot(1)">✕</div></div>`;
            }
            if (s) {
                html += `<div class="preview-item preview-item--edit"><span class="preview-slot-label">2</span><img src="${s}" alt=""><div class="remove-btn" onclick="liraApp.removeImageEditSlot(2)">✕</div></div>`;
            }
            container.innerHTML = html;
            return;
        }

        const list = this.state.pendingAttachments || [];

        if (list.length === 0) {
            container.style.display = 'none';
            container.innerHTML = '';
            return;
        }

        container.style.display = 'flex';

        container.innerHTML = list.map((att) => {
            const safeId = LiraUtils.escapeHTML(att.id);
            if (att.kind === 'document') {
                const label = LiraUtils.escapeHTML(att.name || this.t('document'));
                const hint = LiraUtils.escapeHTML(att.previewUrl || '');
                return `
            <div class="preview-item preview-item--doc">
                <div class="preview-doc-icon">📄</div>
                <div class="preview-doc-name" title="${label}">${label}</div>
                ${hint ? `<div class="preview-doc-hint">${hint}</div>` : ''}
                <div class="remove-btn" onclick="liraApp.removePendingAttachment('${safeId}')">✕</div>
            </div>`;
            }
            const src = att.previewUrl || '';
            return `
            <div class="preview-item">
                <img src="${src}" alt="">
                <div class="remove-btn" onclick="liraApp.removePendingAttachment('${safeId}')">✕</div>
            </div>`;
        }).join('');
    }

    removePendingAttachment(attachmentId) {
        const idx = this.state.pendingAttachments.findIndex((a) => a.id === attachmentId);
        if (idx === -1) return;
        revokeAttachmentPreview(this.state.pendingAttachments[idx]);
        this.state.pendingAttachments.splice(idx, 1);
        if (this.backend?.remove_pending_attachment) {
            this.backend.remove_pending_attachment(attachmentId);
        }
        this.renderMultiImagePreview();
    }

    removeImageEditSlot
    removeImageEditSlot(slot) {
        if (slot === 1) {
            this.state.imageEditPrimaryB64 = null;
            this.state.imageEditSecondaryB64 = null;
            if (this.backend) {
                this.backend.clearImageEditPrimarySlot();
                this.backend.clearImageEditSecondarySlot();
            }
        } else if (slot === 2) {
            this.state.imageEditSecondaryB64 = null;
            if (this.backend) this.backend.clearImageEditSecondarySlot();
        }
        this.renderMultiImagePreview();
    }

    handleSend() {
        const prompt = LiraUtils.$('userInput').value.trim();
        if (!prompt || !this.backend) return;

        if (this.state.isImageEdit) {
            if (!this.state.imageEditPrimaryB64) {
                alert(this.t('Attach the main image first (Photo button). Second is optional.'));
                return;
            }
            this.view.showChatLoader(this.t('Editing image…'));
            setTimeout(() => {
                this.backend.sendMessage(prompt).then(() => {
                    this.state.imageEditPrimaryB64 = null;
                    this.state.imageEditSecondaryB64 = null;
                    this.renderMultiImagePreview();
                });
            }, 50);
            this.view.resetInput();
            return;
        }

        if (this.state.isImageGenerator) {
            const negative = LiraUtils.$('negativeInput').value;
            const ratio = LiraUtils.$('ratioSelect').value;

            // Loading indicator
            this.view.showChatLoader(this.t('Painting your masterpiece…'));

            setTimeout(() => {
                this.backend.process_image_request(prompt, negative, ratio);
            }, 50);

        } else {
            this.view.resetInput();
            this.backend.sendMessage(prompt).then((actualId) => {
                if (this.state.currentSessionId !== actualId) {
                    this.state.currentSessionId = actualId;
                    this.renderHistoryList();
                }
                releaseAllPendingAttachments(this.state.pendingAttachments);
                this.state.pendingAttachments = [];
                this.renderMultiImagePreview();
            });
        }
    }

    openFullscreen(url) {
        if (this.backend && this.backend.open_system_file) {
            this.backend.open_system_file(url);
        } else {
            console.log(this.t('Opening photo:'), url);
        }
    }

    newChat() {
        this._leaveSearchIfNeeded();
        if (this.state.isImageGenerator) {
            this.view.clearChat();
            const msg = this.state.isImageEdit
                ? this.t('Canvas cleared. Attach a new photo and describe the edit.')
                : this.t('Canvas cleared. Ready for new ideas! 🎨');
            this.view.renderSystemMessage(msg);
            this.closeSidebar();
            this.view.hideBlockingLoader();
            return; // Exit without hitting backend or sessions
        }

        this.view.showBlockingLoader(this.t('Creating new chat…'));
        this.backend.create_new_session().then((newId) => {
            this.view.hideBlockingLoader();
            this.state.currentSessionId = newId;
            this.view.clearChat();
            this.view.renderSystemMessage(this.t('New chat started'));
            this.closeSidebar();
            this.renderHistoryList();
        });
    }

    initEvents() {
        LiraUtils.$('chat-wrapper').onclick = (e) => {
            const switcher = LiraUtils.$('model-switcher');
            if (switcher && e.target.id !== 'model-switcher' && !e.target.closest('#model-switcher')) {
                switcher.classList.remove('opened');
            }
        }

        const userInput = LiraUtils.$('userInput');
        if (userInput) {
            userInput.onkeydown = (e) => {
                // Enter works in both modes
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    this.handleSend();
                }
            };
            userInput.oninput = () => this.view.updateInputHeight();
        }
    }

    submitModelParams() {
        const form = LiraUtils.$('model-settings-form');
        const data = Object.fromEntries(new FormData(form).entries());

        if (this.state.isImageEdit) {
            const cb = form.querySelector('input[name="text_encoder_gpu"]');
            data.text_encoder_gpu = Boolean(cb && cb.checked);
        }

        if (this.backend) {
            this.backend.update_model_settings(this.state.modelInfo.id, JSON.stringify(data));

            // 1. Update local state with new settings
            this.state.modelInfo.settings = { ...this.state.modelInfo.settings, ...data };

            // 2. Sync SD artist inputs
            if (this.state.modelInfo.model_class === 'text-to-image') {
                const posInput = document.getElementById('userInput');
                const negInput = document.getElementById('negativeInput');

                if (posInput && data.positive_prefix !== undefined) {
                    posInput.value = data.positive_prefix;
                }
                if (negInput && data.negative_prompt !== undefined) {
                    negInput.value = data.negative_prompt;
                }
            }

            this.view.renderSystemMessage(this.t('Settings saved'));
            this.closeSubMenu();
        }
    }
    
    updateParam(value, displayId) { LiraUtils.$(displayId).innerText = value; }
    toggleSidebar() {
        const sidebar = LiraUtils.$('main-sidebar');
        const overlay = LiraUtils.$('sidebar-overlay');
        sidebar.classList.toggle('opened');
        overlay.classList.toggle('active');
        if (!sidebar.classList.contains("opened")) this.closeSubMenu();
    }
    closeSidebar() {
        LiraUtils.$('main-sidebar').classList.remove('opened');
        LiraUtils.$('sidebar-overlay').classList.remove('active');
        this.closeSubMenu();
    }
    openSubMenu(type) { LiraUtils.$(`sidebar-${type}-list`).classList.toggle('opened'); }

    _leaveSearchIfNeeded() {
        if (this.state.searchMode && this.backend?.leaveSearchMode) {
            this.backend.leaveSearchMode();
            this.state.searchMode = false;
        }
    }

    async openSearch() {
        const chatContainer = document.getElementById('chat');
        if (chatContainer?.classList.contains('gallery-mode')) {
            this.closeGallery();
        }
        this.closeSidebar();
        if (this.backend?.enterSearchMode) {
            await this.backend.enterSearchMode();
            this.state.searchMode = true;
        }
    }

    async openDownloadsFolder() {
        this.closeSidebar();
        if (!this.backend?.openDownloadsFolder) {
            this.showToast(this.t('Opening folder is unavailable'));
            return;
        }
        try {
            const raw = await this.backend.openDownloadsFolder();
            const res = JSON.parse(raw);
            if (res.ok) {
                this.showToast(`📁 ${res.path}`);
            } else {
                this.showToast(this.t('Could not open downloads folder'));
            }
        } catch (e) {
            console.error('[downloads] open folder', e);
            this.showToast(this.t('Error opening folder'));
        }
    }

    onLeftSearchMode() {
        this.state.searchMode = false;
    }
    closeSubMenu() { LiraUtils.$$('.nav-list.level-2').forEach(el => el.classList.remove('opened')); }
    handleSave() { if (this.backend) this.backend.saveExperience(); }
    handleMarkMemory() { if (this.backend) this.backend.markMemoryVerified(); }
    
    editChatPrompt(id, oldTitle) {
        const title = prompt(this.t('Enter a new title:'), oldTitle);
        if (title && title !== oldTitle) this.backend.rename_chat(id, title).then(() => this.renderHistoryList());
    }

    deleteChatPrompt(id) {
        if (confirm(this.t('Delete this chat permanently?'))) {
            this.view.showBlockingLoader(this.t('Deleting…'));
            this.backend.delete_chat(id).then(() => {
                this.view.hideBlockingLoader();
                this.renderHistoryList();
                if (this.state.currentSessionId === id) { this.view.clearChat(); this.state.currentSessionId = null; }
            });
        }
    }

    toggleMute() {
        if (this.backend) {
            this.backend.toggle_mute().then(isMuted => {
                // Find button by HTML id
                const btn = document.getElementById('mute-btn');
                if (btn) {
                    // Toggle icon by state
                    btn.innerText = '🔊';
                    // Optional CSS class (e.g. muted)
                    btn.classList.toggle('muted', isMuted);
                }
                console.log(`${this.t('Mute mode:')} ${isMuted}`);
            });
        }
    }

    updateVolume(val) {
        const numValue = parseFloat(val);
        if (this.backend) {
            this.backend.apply_volume_live(numValue); // Apply volume only (no persist)
        }
        document.getElementById('val-volume').innerText = Math.round(numValue * 100) + '%';
    }

    renderArtCanvas(url, prompt) {
        // 1. Remove chat loader
        this.view.hideChatLoader();

        const container = LiraUtils.$('chat');
        if (!container) return;

        container.innerHTML = '';

        const html = `
            <div class="canvas-container">
                <img src="${url}" class="canvas-image" onload="liraApp.view.scrollToBottom()">
                <div class="canvas-footer">
                    <p class="canvas-prompt-text">✨ ${prompt}</p>
                </div>
            </div>
        `;
        container.insertAdjacentHTML('beforeend', html);
    }

    async openGallery(filter = 'all') {
        this._leaveSearchIfNeeded();
        // Ignore repeat clicks while request in flight
        if (this.state.isGalleryLoading) return;

        if (this.backend?.get_active_model_info) {
            try {
                const data = JSON.parse(await this.backend.get_active_model_info());
                this.state.modelInfo = { ...(this.state.modelInfo || {}), ...data };
            } catch (e) {
                console.warn('[Gallery] get_active_model_info failed', e);
            }
        }
        this.syncGalleryToolsPanel();

        this.state.currentGalleryFilter = filter;
        this.renderGallerySubMenu();
        this.closeSidebar();

        const chatContainer = LiraUtils.$('chat');
        chatContainer.classList.add('gallery-mode');
        this.setGalleryInputCompact(true);

        let gallery = chatContainer.querySelector('.gallery-grid-view');
        if (!gallery) {
            gallery = LiraUtils.create('div', 'gallery-grid-view', '');
            chatContainer.appendChild(gallery);

            const backBtn = document.createElement('div');
            backBtn.className = 'close-gallery-btn';
            backBtn.onclick = () => this.closeGallery();
            backBtn.innerHTML = '<div class="gallery-action-card"></div>';
            gallery.appendChild(backBtn);
        }

        // Enable loading state
        this.state.isGalleryLoading = true;

        // Disable sort button if rendered
        const sortBtn = gallery.querySelector('.sort-toggle-btn');
        if (sortBtn) {
            sortBtn.disabled = true;
            sortBtn.style.opacity = '0.5';
            sortBtn.innerText = this.t('Loading…');
        }

        const currentSort = this.state.gallerySort || 'DESC';

        this.backend.get_all_generations(filter, currentSort, (response) => {
            const images = JSON.parse(response);
            this.state.images = images;

            // Clear old content
            const oldToolbar = gallery.querySelector('.gallery-toolbar');
            if (oldToolbar) oldToolbar.remove();
            gallery.querySelectorAll('.gallery-item, .empty-state').forEach(el => el.remove());

            // Render new content
            this.renderGalleryToolbar(gallery, filter, images.length);
            this.renderGalleryGrid(gallery, images);

            // Release lock
            this.state.isGalleryLoading = false;
        });
    }


    closeGallery() {
        const chatContainer = document.getElementById('chat');
        chatContainer.classList.remove('gallery-mode'); // Messages return to display: flex

        const galleryView = chatContainer.querySelector('.gallery-grid-view');
        if (galleryView) galleryView.remove();

        this.setGalleryInputCompact(false);
        this.syncGalleryToolsPanel();

        // Scroll to end of history
        if (this.view && this.view.scrollToBottom) this.view.scrollToBottom();
    }

    _normalizeGalleryImageRow(raw) {
        if (!raw) return null;
        if (typeof raw === 'string') {
            const path = raw.trim();
            return path ? { id: null, path, prompt: '', description: '' } : null;
        }
        const path = String(raw.path || '').trim();
        if (!path) return null;
        return {
            id: raw.id ?? null,
            path,
            prompt: raw.prompt || '',
            description: raw.description || '',
            model: raw.model,
            date: raw.date,
        };
    }

    _normalizeGalleryImages(list) {
        if (!Array.isArray(list)) return [];
        return list.map((row) => this._normalizeGalleryImageRow(row)).filter(Boolean);
    }

    _galleryPathKey(path) {
        const p = String(path || '');
        return p.startsWith('file://') ? p : `file://${p}`;
    }

    _getViewerImages() {
        const viewer = this.state.viewerImages;
        if (Array.isArray(viewer) && viewer.length > 0) {
            return viewer;
        }
        return this.state.images || [];
    }

    showFullImageFromChat(src) {
        this.showFullImage(src, null, { fromChat: true });
    }

    showFullImage(src, imgData = null, options = {}) {
        const fromChat = Boolean(options.fromChat);
        const showGalleryMeta = !fromChat;

        const overlay = document.createElement('div');
        overlay.id = 'full-image-overlay';

        let list = showGalleryMeta ? [...this._getViewerImages()] : [];
        const srcKey = this._galleryPathKey(src);
        this.state.currentImageIndex = -1;

        if (showGalleryMeta) {
            this.state.currentImageIndex = list.findIndex((img) => {
                return this._galleryPathKey(img.path) === srcKey;
            });
            if (this.state.currentImageIndex < 0 && imgData) {
                const row = this._normalizeGalleryImageRow(imgData);
                if (row) {
                    list.push(row);
                    this.state.viewerImages = list;
                    this.state.currentImageIndex = list.length - 1;
                }
            }
        }

        const hasNav = showGalleryMeta && list.length > 1;
        const contentClass = showGalleryMeta
            ? 'full-view-content'
            : 'full-view-content full-view-content--chat-only';
        const imgClass = showGalleryMeta ? 'full-view-img' : 'full-view-img full-view-img--chat-only';
        const metaHtml = showGalleryMeta
            ? `
                <div class="full-view-meta-container">
                    <div class="full-prompt-actions">
                        <button type="button" class="copy-full-btn" onclick="liraApp.copyCurrentPrompt()">${this.t('Copy prompt')}</button>
                        <button type="button" class="save-desc-full-btn" onclick="liraApp.saveFullImageDescription()">${this.t('Save description')}</button>
                    </div>
                    <label class="full-view-description-label" for="fullImageDescription">${this.t('Description for search')}</label>
                    <textarea id="fullImageDescription" class="full-view-description-input" rows="4"
                        placeholder="${this.t('Briefly: what is in the image…')}"></textarea>
                </div>`
            : '';
        overlay.innerHTML = `
            <div class="close-full-view" onclick="liraApp.closeFullImage()">✕</div>
            <div class="${contentClass}">
                <div class="viewer-container">
                    ${hasNav ? '<button class="nav-arrow prev-arrow" onclick="liraApp.prevImage()">&#10094;</button>' : ''}
                    <img id="fullImageSrc" src="${src}" class="${imgClass}">
                    ${hasNav ? '<button class="nav-arrow next-arrow" onclick="liraApp.nextImage()">&#10095;</button>' : ''}
                </div>
                ${metaHtml}
            </div>
        `;

        overlay.onclick = (e) => {
            if (e.target === overlay) overlay.remove();
        };

        document.body.appendChild(overlay);
        if (showGalleryMeta) {
            const descEl = document.getElementById('fullImageDescription');
            const row = this._normalizeGalleryImageRow(imgData)
                || list[this.state.currentImageIndex];
            if (descEl && row) {
                descEl.value = row.description || '';
            }
            if (hasNav) {
                this.updateNavArrows();
            }
        }
    }

    async saveFullImageDescription() {
        const img = this._getViewerImages()[this.state.currentImageIndex];
        const descEl = document.getElementById('fullImageDescription');
        if (!img || !descEl || !this.backend?.save_generation_description) {
            return;
        }
        const text = descEl.value.trim();
        try {
            const raw = await this.backend.save_generation_description(String(img.id), text);
            const res = JSON.parse(raw);
            if (res.ok) {
                img.description = text;
                for (const arr of [
                    this.state.images,
                    this.state.searchResultImages,
                    this.state.viewerImages,
                ]) {
                    if (!Array.isArray(arr)) continue;
                    const row = arr.find((r) => String(r.id) === String(img.id));
                    if (row) row.description = text;
                }
                this.showToast(this.t('Description saved'));
            } else {
                this.showToast(this.t('Could not save description'));
            }
        } catch (e) {
            console.error(e);
            this.showToast(this.t('Save failed'));
        }
    }

    copyCurrentPrompt() {
        const img = this._getViewerImages()[this.state.currentImageIndex];
        if (img && this.backend) {
            if (this.backend && this.backend.copy_to_clipboard) {
                const prompt = (img.prompt || '').trim();
                if (!prompt) {
                    this.showToast(this.t('Prompt is empty'));
                    return;
                }
                this.backend.copy_to_clipboard(prompt);
                this.showToast(this.t('Prompt copied'));
            } else {
                console.error(this.t('Copy backend unavailable'));
            }
        }
    }

    closeFullImage() {
        const overlay = document.getElementById('full-image-overlay');
        if (overlay) overlay.remove();
    }

    nextImage() {
        const list = this._getViewerImages();
        if (this.state.currentImageIndex < list.length - 1) {
            this.state.currentImageIndex++;
            this.updateOverlayFromIndex();
        } else {
            // Optional: wrap to first image
            // this.state.currentImageIndex = 0;
            // this.updateOverlayFromIndex();
        }
    }

    // Left arrow handler
    prevImage() {
        if (this.state.currentImageIndex > 0) {
            this.state.currentImageIndex--;
            this.updateOverlayFromIndex();
        } else {
            // Optional: wrap to last image
            // this.state.currentImageIndex = this.images.length - 1;
            // this.updateOverlayFromIndex();
        }
    }

    updateOverlayFromIndex() {
        const imgData = this._getViewerImages()[this.state.currentImageIndex];
        const imgTarget = document.getElementById('fullImageSrc');
        const descEl = document.getElementById('fullImageDescription');
        imgTarget.src = imgData.path.startsWith('file://') ? imgData.path : `file://${imgData.path}`;
        if (descEl) {
            descEl.value = imgData.description || '';
        }

        this.updateNavArrows();
    }

    // Hide arrows at list ends
    updateNavArrows() {
        const prevBtn = document.querySelector('.prev-arrow');
        const nextBtn = document.querySelector('.next-arrow');

        // Hide left arrow on first image
        prevBtn.style.display = (this.state.currentImageIndex === 0) ? 'none' : 'block';

        // Hide right arrow on last image
        const list = this._getViewerImages();
        nextBtn.style.display = (this.state.currentImageIndex === list.length - 1) ? 'none' : 'block';
    }

    _canAttachGalleryImageToChat() {
        return !this.state.isImageGenerator
            && !this.state.isImageEdit
            && Boolean(this.backend?.register_image_attachment_from_path);
    }

    async attachGalleryImageToChat(img, event) {
        event?.stopPropagation();
        if (!this._canAttachGalleryImageToChat()) {
            this.showToast(this.t('Attachments are only available in chat with a multimodal model'));
            return;
        }
        const path = (img.path || '').replace(/^file:\/\//, '');
        if (!path) {
            this.showToast(this.t('No file path'));
            return;
        }
        try {
            const raw = await this.backend.register_image_attachment_from_path(path);
            const data = this._parseAttachmentRegisterResponse(raw);
            if (data) {
                this._pushPendingAttachment(data);
                this.showToast(this.t('Image attached to message'));
            }
        } catch (e) {
            console.error('[Gallery] attach to chat', e);
            this.showToast(this.t('Could not attach image'));
        }
    }

    renderGalleryGrid(container, images) {
        if (!images || images.length === 0) {
            container.appendChild(LiraUtils.create('div', 'empty-state', this.t('Gallery is empty for now')));
            return;
        }

        const showAttachBtn = this._canAttachGalleryImageToChat();

        images.forEach(img => {
            const item = LiraUtils.create('div', 'gallery-item');
            const fullPath = img.path.startsWith('file://') ? img.path : `file://${img.path}`;

            // Main image
            const image = LiraUtils.create('img');
            image.src = fullPath;
            image.loading = 'lazy';
            image.onclick = () => {
                this.state.viewerImages = this.state.images;
                this.showFullImage(fullPath, img);
            };
            item.appendChild(image);

            // Info layer (hover from CSS)
            const info = LiraUtils.create('div', 'art-info', `
                <div class="art-model-name">👤 ${img.model}</div>
                <div class="art-date">📅 ${img.date}</div>
            `);
            item.appendChild(info);

            // Copy prompt button
            const copyBtn = LiraUtils.create('div', 'copy-prompt-btn', '📋');
            copyBtn.onclick = (e) => {
                e.stopPropagation();

                // Call via backend bridge
                if (this.backend && this.backend.copy_to_clipboard) {
                    this.backend.copy_to_clipboard(img.prompt);
                    this.showToast(this.t('Prompt copied'));
                } else {
                    console.error(this.t('Copy backend unavailable'));
                }
            };
            item.appendChild(copyBtn);

            if (showAttachBtn) {
                const attachBtn = LiraUtils.create('div', 'attach-to-chat-btn', '📎');
                attachBtn.title = this.t('Attach to message for analysis');
                attachBtn.onclick = (e) => this.attachGalleryImageToChat(img, e);
                item.appendChild(attachBtn);
            }

            // Delete button (existing)
            const deleteBtn = LiraUtils.create('div', 'delete-art-btn', '🗑️');
            deleteBtn.onclick = (e) => {
                e.stopPropagation();
                if (confirm(this.t('Delete this artwork?'))) this.deleteImage(img.id, item);
            };
            item.appendChild(deleteBtn);

            container.appendChild(item);
        });
    }

    deleteImage(id, element) {
        // Coerce string to number via Number() or unary +
        const numericId = Number(id);
        this.state.images = this.state.images.filter(img => img.id !== id);

        this.backend.delete_generation_entry(numericId).then((res) => {
            const response = JSON.parse(res);
            if (response.status === 'success') {
                element.classList.add('removing');
                setTimeout(() => element.remove(), 300);
            }
        });
    }

    toggleInputWrapper(show) {
        const wrapper = document.getElementById('input-container-wrapper');
        if (wrapper) {
            wrapper.style.display = show ? 'block' : 'none';
        }
    }

    toggleHistoryGallery(isImageGenerator) {
        const historyItem = document.getElementById('nav-item-history');
        const galleryItem = document.getElementById('nav-item-gallery');

        if (historyItem && galleryItem) {
            // Gallery always visible now
            galleryItem.style.display = 'block';
            // History for text models only
            historyItem.style.display = isImageGenerator ? 'none' : 'block';
        }
    }

    async renderGallerySubMenu() {
        const el = LiraUtils.$('sidebar-gallery-list');
        if (!el) return;

        const data = await this.backend.get_full_config();
        const config = JSON.parse(data);

        el.innerHTML = `<button onclick="liraApp.closeSubMenu()" class="back-btn">${this.t('Back')}</button>`;
        const list = LiraUtils.create('ul', 'history-list');

        // 1. Check “All works”
        const isAllActive = (this.state.currentGalleryFilter === 'all');
        const allItem = LiraUtils.create('li', `history-item ${isAllActive ? 'active' : ''}`, `
            <div class="history-info" onclick="liraApp.openGallery('all')">
                <div class="history-title">🖼️ ${this.t('All works')}</div>
            </div>`);
        list.appendChild(allItem);

        // 2. Model list
        config.models.forEach(model => {
            if (model.model_class === 'text-to-image' || model.model_class === 'image-edit') {
                const isActive = (this.state.currentGalleryFilter === model.name);
                const item = LiraUtils.create('li', `history-item ${isActive ? 'active' : ''}`, `
                    <div class="history-info" onclick="liraApp.openGallery('${model.name}')">
                        <div class="history-title">${model.name}</div>
                    </div>`);
                list.appendChild(item);
            }
        });

        el.appendChild(list);
    }


    renderGalleryToolbar(container, filter, count) {
        const sortIcon = this.state.gallerySort === 'DESC'
            ? this.t('Newest first')
            : this.t('Oldest first');

        // Build panel via LiraUtils
        const toolbar = LiraUtils.create('div', 'gallery-toolbar', `
            <div class="toolbar-left">
                <div class="filter-indicator">
                    <span class="filter-label">${this.t('Gallery')}</span>
                    <span class="filter-value">${filter === 'all' ? this.t('All works') : filter}</span>
                </div>
                <div class="gallery-stats">${this.t('{count} images', { count })}</div>
            </div>
            <div class="toolbar-right">
                <button type="button" class="sort-toggle-btn" onclick="liraApp.toggleSort()">
                    ${sortIcon}
                </button>
            </div>
        `);

        // Insert before image grid
        container.prepend(toolbar);
    }

    toggleSort() {
        this.state.gallerySort = (this.state.gallerySort === 'DESC') ? 'ASC' : 'DESC';
        // Reopen gallery with same filter, new sort
        this.openGallery(this.state.currentGalleryFilter || 'all');
    }

    async startGalleryDescribe(repair = false) {
        const taskId = repair ? 'gallery_describe_repair' : 'gallery_describe_missing';
        await this.runSidebarModelTask(taskId);
    }

    openGalleryDescribeModal(total) {
        const modal = document.getElementById('gallery-describe-modal');
        if (!modal) return;
        modal.classList.add('is-open');
        modal.setAttribute('aria-hidden', 'false');
        this.state.galleryDescribeRunning = true;
        const cancelBtn = document.getElementById('gallery-describe-cancel-btn');
        if (cancelBtn) cancelBtn.disabled = false;
        const n = total == null ? 0 : Number(total);
        if (n > 0) {
            this.updateGalleryDescribeUi({
                type: 'started',
                total: n,
                done: 0,
            });
        } else {
            const status = document.getElementById('gallery-describe-status');
            const file = document.getElementById('gallery-describe-file');
            if (status) {
                status.textContent = this.t('Preparing… releasing GPU and starting description');
            }
            if (file) {
                file.textContent = '';
            }
        }
    }

    closeGalleryDescribeModal() {
        const modal = document.getElementById('gallery-describe-modal');
        if (modal) {
            modal.classList.remove('is-open');
            modal.setAttribute('aria-hidden', 'true');
        }
        this.state.galleryDescribeRunning = false;
    }

    cancelGalleryDescribe() {
        if (this.backend?.cancel_gallery_description_refresh) {
            this.backend.cancel_gallery_description_refresh();
        }
        const cancelBtn = document.getElementById('gallery-describe-cancel-btn');
        if (cancelBtn) cancelBtn.disabled = true;
    }

    async onGalleryDescribeEvent(payload) {
        let data = payload;
        if (typeof payload === 'string') {
            try {
                data = JSON.parse(payload);
            } catch (e) {
                return;
            }
        }
        const silent = Boolean(data.silent);
        if (data.type === 'rejected' && !silent) {
            this.closeGalleryDescribeModal();
            this.showToast(data.error || data.message || this.t('Failed to start'));
            return;
        }
        if (data.type === 'started') {
            if (!silent) {
                if (this.state.galleryDescribeRunning) {
                    this.updateGalleryDescribeUi({
                        ...data,
                        type: 'started',
                    });
                } else {
                    this.openGalleryDescribeModal(data.total || 0);
                }
            }
            return;
        }
        if (!silent) {
            this.updateGalleryDescribeUi(data);
        }
        if (data.type === 'finished') {
            if (!silent) {
                const saved = Number(data.saved);
                const missing = Number(data.missing_remaining);
                const done = data.done || 0;
                const total = data.total || 0;
                let msg = this.t('Done: {done} of {total}', { done, total });
                if (Number.isFinite(saved)) {
                    msg = this.t('Descriptions saved: {saved} of {total}', { saved, total });
                }
                if (Number.isFinite(missing) && missing > 0) {
                    msg += this.t('. Still without description: {missing}', { missing });
                } else if (Number.isFinite(saved) && saved === 0 && total > 0) {
                    msg = this.t('Model returned no description text — see logs/lira.log');
                }
                this.showToast(msg);
                this.closeGalleryDescribeModal();
            }
            this._galleryDescribeHintModelId = null;
            if (Number.isFinite(Number(data.missing_remaining))) {
                this._applyGalleryDescribeBadgeState(data.missing_remaining);
            }
            await this.refreshGalleryDescribeBadges();
        } else if (data.type === 'cancelled') {
            if (!silent) {
                this.showToast(this.t('Cancel', {
                    done: data.done || 0,
                    total: data.total || 0,
                }));
                this.closeGalleryDescribeModal();
            }
            await this.refreshGalleryDescribeBadges();
        } else if (data.type === 'progress' && Number.isFinite(Number(data.missing_remaining))) {
            this._applyGalleryDescribeBadgeState(data.missing_remaining);
        }
    }

    updateGalleryDescribeUi(data) {
        const status = document.getElementById('gallery-describe-status');
        const file = document.getElementById('gallery-describe-file');
        if (!status) return;
        const total = data.total || 0;
        const done = data.done || 0;
        if (data.type === 'progress') {
            status.textContent = this.t('Processed {done} of {total}…', { done, total });
            if (file) {
                const name = data.current_path || '';
                file.textContent = name ? this.t('File {name} is too large. Max 6 MB.', { name }) : '';
            }
        } else if (data.type === 'redescribe') {
            status.textContent = this.t(
                'Retry for {count} with English prefix / tags ({done}/{total})…',
                { count: data.count || 0, done, total },
            );
            if (file) file.textContent = this.t('Regenerating descriptions');
        } else if (data.type === 'handoff') {
            if (data.phase === 'release') {
                status.textContent = this.t('{total} total: releasing GPU for batch description…', { total });
                if (file) file.textContent = this.t('Chat paused for now; faster than CPU');
            } else {
                status.textContent = this.t('Processed {done} of {total}…', { done, total });
                if (file) file.textContent = '';
            }
        } else if (data.type === 'started') {
            const sub = data.gpu_handoff
                ? ` ${this.t('— GPU, separate process')}`
                : (data.subprocess ? ` ${this.t('— background process')}` : '');
            status.textContent = this.t('{total} to describe{suffix}', { total, suffix: sub });
            if (file) {
                file.textContent = data.gpu_handoff
                    ? this.t('Batch description on GPU starting soon…')
                    : '';
            }
        }
    }

    async checkGalleryDescriptionsPending() {
        if (!LiraState.modelSupportsGalleryDescribe(this.state.modelInfo)) {
            return;
        }
        await this.refreshGalleryDescribeBadges();
        const missing = Number(this._galleryMissingDescriptionCount) || 0;
        if (missing < 1) {
            return;
        }
        const modelId = String(this.state.modelInfo?.id ?? '');
        if (!modelId || this._galleryDescribeHintModelId === modelId) {
            return;
        }
        this._galleryDescribeHintModelId = modelId;
        this.showToast(this.t(
            '{missing} gallery image(s) without description. Open Tools on the left and tap Add descriptions.',
            { missing },
        ));
        this.toggleLimbicToolsMenuOpen(true);
    }

    toggleLimbicToolsMenuOpen(open = true) {
        const menu = document.getElementById('limbic-tools-menu');
        if (!menu) return;
        const isOpen = menu.classList.contains('limbic-tools-menu--open');
        if (open && !isOpen) {
            this.toggleLimbicToolsMenu();
        } else if (!open && isOpen) {
            this.toggleLimbicToolsMenu();
        }
    }

    _setGalleryBadgeVisible(el, visible, text = '') {
        if (!el) return;
        const show = Boolean(visible);
        el.hidden = !show;
        el.classList.toggle('is-hidden', !show);
        el.textContent = show ? text : '';
        el.setAttribute('aria-hidden', show ? 'false' : 'true');
    }

    _applyGalleryDescribeBadgeState(missing) {
        const n = Math.max(0, Number(missing) || 0);
        this._galleryMissingDescriptionCount = n;
        const needsAttention = n > 0;
        const countBadge = document.getElementById('gallery-describe-missing-badge');
        const alertBadge = document.getElementById('limbic-tools-alert-badge');
        const attentionButtons = document.querySelectorAll('[data-attention-when-missing="1"]');
        const toggle = document.getElementById('limbic-tools-toggle');

        this._setGalleryBadgeVisible(
            countBadge,
            needsAttention,
            n > 99 ? '99+' : String(n),
        );
        this._setGalleryBadgeVisible(alertBadge, needsAttention, '!');

        attentionButtons.forEach((btn) => {
            btn.classList.toggle('limbic-tool-btn--attention', needsAttention);
        });
        toggle?.classList.toggle('limbic-tools-toggle--attention', needsAttention);
        if (!needsAttention) {
            this._galleryDescribeHintModelId = null;
        }
    }

    async refreshGalleryDescribeBadges() {
        if (!LiraState.modelSupportsGalleryDescribe(this.state.modelInfo)) {
            this._applyGalleryDescribeBadgeState(0);
            return;
        }
        let missing = 0;
        if (this.backend?.count_gallery_description_repair) {
            try {
                const raw = await this.backend.count_gallery_description_repair();
                const counts = typeof raw === 'string' ? JSON.parse(raw) : raw;
                missing = Number(counts?.missing) || 0;
            } catch (e) {
                console.warn('[gallery] badge count failed', e);
            }
        }
        this._applyGalleryDescribeBadgeState(missing);
    }

    clearGalleryDescribeHint() {
        void this.refreshGalleryDescribeBadges();
    }

    showToast(message) {
        // Create toast element
        const toast = LiraUtils.create('div', 'lira-toast', message);
        document.body.appendChild(toast);

        // Fade out and remove after 1.5s
        setTimeout(() => {
            toast.classList.add('fade-out');
            setTimeout(() => toast.remove(), 300);
        }, 1200);
    }

    getActiveLoras() {
        const prompt = document.getElementById('userInput').value || "";
        // Regex for all <lora:NAME:WEIGHT>
        const matches = [...prompt.matchAll(/<lora:([^:]+):([^>]+)>/g)];
        return matches.map(m => m[1]); // Return applied LoRA names
    }

    async openLoraModal() {
        const modal = document.getElementById('lora-modal');
        const container = document.getElementById('lora-list-container');

        // Show loader or clear container
        container.innerHTML = `<div class="loading">${this.t('Scanning models folder…')}</div>`;
        modal.style.display = 'flex';

        // 1. LoRAs referenced in prompt
        const currentPrompt = document.getElementById('userInput').value || "";
        const activeLoras = [...currentPrompt.matchAll(/<lora:([^:]+):/g)].map(m => m[1]);

        // 2. Fetch file list from backend
        const response = await this.backend.get_lora_list();
        const loras = JSON.parse(response);

        container.innerHTML = ''; // Clear loader

        if (loras.length === 0) {
            container.innerHTML = `<div class="empty">${this.t('No LoRA files in /models/lora/')}</div>`;
            return;
        }

        // 3. Render grid
        loras.forEach(name => {
            const isActive = activeLoras.includes(name);
            const card = LiraUtils.create('div', `lora-card ${isActive ? 'active' : ''}`, `
                <div class="lora-name">${name}</div>
                ${isActive ? `<div class="lora-status">${this.t('Already in prompt')}</div>` : ''}
            `);

            if (!isActive) {
                card.onclick = () => this.applyLora(name);
            }
            container.appendChild(card);
        });
    }

    closeLoraModal() {
        document.getElementById('lora-modal').style.display = 'none';
    }

    applyLora(name) {
        const input = document.getElementById('userInput');
        // Append with a space
        input.value = input.value.trim() + ` <lora:${name}:1.0>`;
        this.closeLoraModal();
        this.showToast(this.t('LoRA {name} added', { name }));
    }

    renderGallerySearch(jsonPayload) {
        const data = typeof jsonPayload === 'string' ? JSON.parse(jsonPayload) : jsonPayload;
        const role = data?.role || 'model';
        const images = this._normalizeGalleryImages(
            data?.images ?? (Array.isArray(data) ? data : []),
        );
        this.state.searchResultImages = images;
        const icon = this.state.modelInfo.icon;

        this.addMessage(role, '', icon);

        // 2. Find element
        const lastMsg = this.view.chatContainer.lastElementChild;
        const bubble = lastMsg.querySelector('.bubble') || lastMsg;

        // 3. Insert grid
        if (images.length > 0) {
            const grid = document.createElement('div');
            grid.className = 'search-results-grid';

            images.forEach((img) => {
                const src = this._galleryPathKey(img.path);
                const item = document.createElement('div');
                item.className = 'search-item';
                const image = document.createElement('img');
                image.src = src;
                image.loading = 'lazy';
                image.onclick = () => {
                    this.state.viewerImages = this.state.searchResultImages;
                    this.showFullImage(src, img);
                };
                item.appendChild(image);
                grid.appendChild(item);
            });

            bubble.appendChild(grid);
        }
        this.view.scrollToBottom();
    }


    async updateUI() {
        if (this.i18n?.locale) {
            this.i18n.applyDom();
        }
        const data = await this.backend.get_full_config();
        const config = JSON.parse(data);
        if (config.ui_locale && config.ui_locale !== this.i18n.locale) {
            await this.i18n.load(config.ui_locale);
            this.i18n.applyDom();
        }
        this.state.updateFromConfig(config);
        this.renderModelList(config.models, config.active_model_id);
        this.renderSidebar(config.models, config.active_model_id);
        this.renderHistoryList();
        const galleryOpen = document.getElementById('chat')?.classList.contains('gallery-mode');
        if (!galleryOpen) {
            this.closeGallery();
        }
        this.renderInputPanel();
        if (galleryOpen && this.state.currentGalleryFilter !== undefined) {
            this.setGalleryInputCompact(true);
        }
        this.toggleHistoryGallery(this.state.isImageGenerator);
        await this.renderGallerySubMenu();
        await this.refreshLimbicUI();
        this.syncGalleryToolsPanel();
    }
}