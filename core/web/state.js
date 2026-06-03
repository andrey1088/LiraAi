/**
 * MODEL: Application state
 */
export class LiraState {
    /** Multimodal model in config (clip) — gallery describe from chat. */
    static modelSupportsGalleryDescribe(model) {
        if (!model) return false;
        const mc = model.model_class;
        if (mc === 'text-to-image' || mc === 'image-edit') return false;
        if (model.has_gallery_vision != null) {
            return Boolean(model.has_gallery_vision);
        }
        return Boolean(String(model.clip_model_path || '').trim());
    }

    constructor() {
        this.modelInfo = null;
        this.currentSessionId = null;
        this.isImageGenerator = false;
        this.isImageEdit = false;
        this.sessions = [];
        this.images = [];
        /** Subset for fullscreen view (chat search); else images. */
        this.viewerImages = [];
        this.searchResultImages = [];
        this.currentImageIndex = -1;
        this.currentGalleryFilter = 'all';
        this.gallerySort = 'DESC';
        this.pendingAttachments = [];
        this.imageEditPrimaryB64 = null;
        this.imageEditSecondaryB64 = null;
        this.limbicImagesBase = null;
        this.searchMode = false;
    }

    updateFromConfig(config) {
        const prevId = this.modelInfo ? String(this.modelInfo.id) : null;
        if (config.active_session_id) {
            this.currentSessionId = config.active_session_id;
        }
        const current = config.models.find(m => String(m.id) === String(config.active_model_id));

        if (current) {
            current.user_icon = config.user_icon_base_64;
            current.icon = current.icon_base64; // Sync icon keys
            const newId = String(current.id);
            if (prevId !== null && prevId !== newId) {
                if (window.liraApp) {
                    window.liraApp.clearPendingAttachmentsOnModelSwitch();
                    window.liraApp._galleryDescribeHintModelId = null;
                }
                this.pendingAttachments = [];
                this.imageEditPrimaryB64 = null;
                this.imageEditSecondaryB64 = null;
            }
            this.modelInfo = current;
            this.modelInfo.has_gallery_vision = LiraState.modelSupportsGalleryDescribe(current);
            const mc = this.modelInfo.model_class;
            this.isImageEdit = (mc === 'image-edit');
            this.isImageGenerator = (mc === 'text-to-image' || mc === 'image-edit');
        }
    }
}
