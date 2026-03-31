function prksVideoEscapeAttr(s) {
    if (s == null) return '';
    return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function prksYoutubeEmbedUrl(sourceUrl, providerId) {
    const pid = (providerId || '').trim();
    if (pid) return `https://www.youtube.com/embed/${encodeURIComponent(pid)}`;
    try {
        const u = new URL(sourceUrl);
        const host = (u.hostname || '').toLowerCase();
        if (host.includes('youtu.be')) {
            const id = u.pathname.replace(/^\//, '').split('/')[0];
            if (id) return `https://www.youtube.com/embed/${encodeURIComponent(id)}`;
        }
        if (host.includes('youtube.com')) {
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
            <div style="width:100%; height:100%; padding:10px;">
                <iframe
                    src="${prksVideoEscapeAttr(embed)}"
                    style="width:100%; height:100%; border:0; background: var(--surface);"
                    allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
                    allowfullscreen
                    title="Video player"></iframe>
            </div>
        </div>
    `;
}

window.renderVideoViewerPane = renderVideoViewerPane;

