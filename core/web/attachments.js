/** Attachment size limit (synced with attachment_text.MAX_ATTACHMENT_BYTES). */
export const MAX_ATTACHMENT_BYTES = 6 * 1024 * 1024;

/** Raw bytes → base64 without readAsDataURL (for chunks). */
export function arrayBufferToBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = '';
    const step = 0x8000;
    for (let i = 0; i < bytes.length; i += step) {
        binary += String.fromCharCode.apply(null, bytes.subarray(i, i + step));
    }
    return btoa(binary);
}

/**
 * Chat attachment previews: id, revoke blob: URLs.
 */
export function revokeAttachmentPreview(att) {
    if (!att?.previewUrl) return;
    if (typeof att.previewUrl === 'string' && att.previewUrl.startsWith('blob:')) {
        URL.revokeObjectURL(att.previewUrl);
    }
}

export function releaseAllPendingAttachments(list) {
    if (!Array.isArray(list)) return;
    for (const att of list) {
        revokeAttachmentPreview(att);
    }
}
