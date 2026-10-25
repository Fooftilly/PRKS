"""Shared real-PDF readiness checks for Chromium and Firefox/WebKit E2E.

A PDF toolbar or a rendered DOM wrapper alone does not prove the viewer
mounted, produced a page image, and initialized its synchronization state.
Both browser suites use the exact same observable readiness contract.
"""

def _wait_pdf_viewer(page):
    page.wait_for_selector('[data-prks-role="pdf-viewer"] .prks-pdf-page', timeout=30000)
    page.wait_for_selector('[data-prks-role="pdf-viewer"] .prks-pdf-render-image', timeout=30000)
    page.wait_for_function(
        """() => {
            const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
            const pdf = ctx && ctx.getResource ? ctx.getResource('pdf') : null;
            const v = pdf && pdf.viewer;
            return !!(v && v.getPageCount && v.getPageCount() >= 1);
        }""",
        timeout=30000,
    )
    page.wait_for_function(
        """() => {
            const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
            const pdf = ctx && ctx.getResource ? ctx.getResource('pdf') : null;
            return !!(pdf && pdf.syncState);
        }""",
        timeout=30000,
    )


def _wait_pdf_tab(page, tab_id):
    page.wait_for_function(
        """(tabId) => {
            const ctx = window.prksGetTabContext && window.prksGetTabContext(tabId);
            const pdf = ctx && ctx.getResource ? ctx.getResource('pdf') : null;
            const v = pdf && pdf.viewer;
            return !!(v && v.getPageCount && v.getPageCount() >= 1 && pdf.syncState);
        }""",
        arg=tab_id,
        timeout=30000,
    )
