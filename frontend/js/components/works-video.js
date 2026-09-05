function prksVideoEscapeAttr(s) {
    if (s == null) return '';
    return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function prksYoutubeEmbedUrl(sourceUrl, providerId) {
    const pid = (providerId || '').trim();
    if (pid) return `https://www.youtube.com/embed/${encodeURIComponent(pid)}`;
    // Explicit recognized hosts, not substring matching (rejects
    // "notyoutube.com" / "youtube.com.example.org"). Shared contract with
    // backend _is_youtube_host() and frontend prksIsRecognizedYoutubeHost().
    const YOUTUBE_HOSTS = new Set(['youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be']);
    try {
        const u = new URL(sourceUrl);
        const host = (u.hostname || '').toLowerCase();
        if (!YOUTUBE_HOSTS.has(host)) return '';
        if (host === 'youtu.be') {
            const id = u.pathname.replace(/^\//, '').split('/')[0];
            if (id) return `https://www.youtube.com/embed/${encodeURIComponent(id)}`;
        } else {
            const v = u.searchParams.get('v') || '';
            if (v) return `https://www.youtube.com/embed/${encodeURIComponent(v)}`;
            const parts = u.pathname.replace(/^\//, '').split('/');
            if (parts[0] === 'embed' && parts[1]) return `https://www.youtube.com/embed/${encodeURIComponent(parts[1])}`;
        }
    } catch (_e) {}
    return '';
}

function renderVideoViewerPane(work) {
    const srcUrl = (work && work.source_url) || '';
    const provider = (work && work.provider) || 'youtube';
    const embed =
        provider === 'youtube' ? prksYoutubeEmbedUrl(srcUrl, work.provider_id || '') : '';
    if (!embed) {
        const safe = prksVideoEscapeAttr(srcUrl);
        return `<div class="work-pdf-pane work-pdf-pane--empty"><p class="work-pdf-empty">No embeddable video URL. <a href="${safe}" target="_blank" rel="noopener">Open link</a></p></div>`;
    }
    return `
        <div class="work-pdf-pane">
            <div class="prks-work-video-frame">
                <iframe
                    src="${prksVideoEscapeAttr(embed)}"
                    allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
                    allowfullscreen
                    title="Video player"></iframe>
            </div>
        </div>
    `;
}

window.renderVideoViewerPane = renderVideoViewerPane;

