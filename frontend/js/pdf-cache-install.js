/**
 * Page → worker whole-file PDF cache install.
 *
 * The worker replies only after cache.put settles. A large PDF can take
 * longer than a couple of seconds to clone and store, so
 * PDF_CACHE_INSTALL_ACK_TIMEOUT_MS is a communication-loss bound, not a
 * limit on put latency. 'unacknowledged' means the worker never answered;
 * 'rejected' means it answered {ok:false} or the post never left the page.
 */

export const PDF_CACHE_INSTALL_ACK_TIMEOUT_MS = 120000;

export function prksPostPdfCacheInstall(controller, pathname, body, options) {
    const opts = options || {};
    const Channel = opts.MessageChannel
        || (typeof MessageChannel !== 'undefined' ? MessageChannel : null);
    const timeoutMs = typeof opts.timeoutMs === 'number' && opts.timeoutMs >= 0
        ? opts.timeoutMs
        : PDF_CACHE_INSTALL_ACK_TIMEOUT_MS;
    if (!controller || typeof controller.postMessage !== 'function' || typeof Channel !== 'function') {
        return Promise.resolve('rejected');
    }
    if (!(body instanceof ArrayBuffer) || body.byteLength < 1) {
        return Promise.resolve('rejected');
    }
    return new Promise(function (resolve) {
        let settled = false;
        function finish(outcome) {
            if (settled) return;
            settled = true;
            resolve(outcome);
        }
        const channel = new Channel();
        const timer = setTimeout(function () {
            finish('unacknowledged');
        }, timeoutMs);
        channel.port1.onmessage = function (event) {
            clearTimeout(timer);
            const data = event && event.data;
            finish(data && data.ok ? 'ok' : 'rejected');
        };
        try {
            controller.postMessage({
                type: 'prks-pdf-cache-install',
                pathname: pathname,
                buffer: body,
            }, [channel.port2, body]);
        } catch (_ePost) {
            clearTimeout(timer);
            finish('rejected');
        }
    });
}

export function prksPdfCacheInstallOutcome(outcome) {
    if (outcome === 'ok') return true;
    if (outcome === 'unacknowledged') {
        throw new Error('PDF cache install unacknowledged');
    }
    return false;
}
