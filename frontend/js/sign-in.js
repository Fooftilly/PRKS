/**
 * PRKS sign-in -- the page a browser with no session sees (PG-C2 #641).
 *
 * Every web process authenticates (`PRKS_AUTH=session` by default), so a
 * browser that is not signed in gets 401 from every `/api/` route. When the
 * page's first session read answers 401 (`prksBrowserScope.needsSignIn()`),
 * this module shows one dialog over the application: Login, Password and
 * Sign in. A successful `POST /api/login` reloads the page, so the browser
 * scope and every store bind to the signed-in account from the start (a
 * page's scope is fixed once resolved). Nothing else changes: an unreachable
 * server, a page bound offline and a testing instance in `loopback-owner`
 * mode (whose session read always succeeds) never see it.
 *
 * The dialog uses the shared dialog family (`.modal-backdrop`, `.modal`,
 * `.modal-header`, `.modal-body`) and the field and button primitives
 * (DESIGN.md "Dialogs", "Fields and forms", "Buttons"). It has no close
 * control and ignores Escape: there is nothing behind it to return to. The
 * application behind it is `inert` while it is open, and its keyboard
 * handlers see no key (stopped at the window in the capture phase).
 *
 * Messages are fixed text: the server's code chooses one; a password or
 * login is never logged or echoed.
 */
(function (root) {
    'use strict';

    const DIALOG_ID = 'prks-sign-in';
    const BACKDROP_ID = 'prks-sign-in-backdrop';

    /** The fixed message for a refused sign-in (`result` from `prksBrowserScope.signIn`). */
    function messageFor(result) {
        if (!result || result.ok) return '';
        if (result.status === 429) {
            const wait = result.retryAfter;
            return wait
                ? 'Too many sign-in attempts. Try again in ' + wait + (wait === 1 ? ' second.' : ' seconds.')
                : 'Too many sign-in attempts. Try again later.';
        }
        if (result.error === 'invalid_credentials') return 'The login or password is not right.';
        if (result.error === 'unreachable' || result.error === 'unavailable') {
            return 'PRKS did not answer. Check that it is running, then try again.';
        }
        if (result.error === 'invalid_request') return 'Enter a login and a password.';
        return 'PRKS could not sign you in. Try again.';
    }

    function element(doc, tag, attrs, text) {
        const el = doc.createElement(tag);
        Object.keys(attrs || {}).forEach(function (name) { el.setAttribute(name, attrs[name]); });
        if (text) el.textContent = text;
        return el;
    }

    /**
     * Builds the dialog in `doc` and wires it to `scope.signIn`. Returns
     * `{ dialog, submit }`; `reload` runs after a successful sign-in.
     */
    function showSignIn(doc, scope, reload) {
        const existing = doc.getElementById(DIALOG_ID);
        if (existing) return { dialog: existing, submit: null };

        // Everything else in the body is inert, also what mounts later.
        function isOwn(el) { return el.id === DIALOG_ID || el.id === BACKDROP_ID; }
        function makeInert(el) { if (el.nodeType === 1 && !isOwn(el)) el.setAttribute('inert', ''); }
        Array.prototype.slice.call(doc.body.children).forEach(makeInert);
        const Observer = doc.defaultView && doc.defaultView.MutationObserver;
        if (typeof Observer === 'function') {
            new Observer(function (records) {
                records.forEach(function (rec) { Array.prototype.slice.call(rec.addedNodes).forEach(makeInert); });
            }).observe(doc.body, { childList: true });
        }

        const backdrop = element(doc, 'div', { id: BACKDROP_ID, class: 'modal-backdrop' });
        const dialog = element(doc, 'div', {
            id: DIALOG_ID,
            class: 'modal',
            role: 'dialog',
            'aria-modal': 'true',
            'aria-labelledby': DIALOG_ID + '-title',
        });
        const header = element(doc, 'div', { class: 'modal-header' });
        header.appendChild(element(doc, 'h3', { id: DIALOG_ID + '-title' }, 'Sign in to PRKS'));
        dialog.appendChild(header);

        const body = element(doc, 'div', { class: 'modal-body' });
        const form = element(doc, 'form', { class: 'prks-stack', novalidate: '' });

        function field(id, label, type, autocomplete) {
            const wrap = element(doc, 'div', { class: 'prks-field' });
            wrap.appendChild(element(doc, 'label', { class: 'prks-field__label', for: id }, label));
            const input = element(doc, 'input', {
                id: id,
                class: 'prks-input',
                type: type,
                name: id,
                autocomplete: autocomplete,
                required: '',
                'aria-required': 'true',
                'aria-describedby': DIALOG_ID + '-error',
                spellcheck: 'false',
            });
            wrap.appendChild(input);
            form.appendChild(wrap);
            return input;
        }
        const login = field(DIALOG_ID + '-login', 'Login', 'text', 'username');
        const password = field(DIALOG_ID + '-password', 'Password', 'password', 'current-password');
        login.setAttribute('autocapitalize', 'none');

        const error = element(doc, 'p', {
            id: DIALOG_ID + '-error',
            class: 'prks-field__error',
            role: 'alert',
            'aria-live': 'polite',
        });
        error.hidden = true;
        form.appendChild(error);

        const actions = element(doc, 'div', { class: 'prks-form-actions' });
        const button = element(doc, 'button', { type: 'submit', class: 'prks-btn prks-btn--primary' }, 'Sign in');
        actions.appendChild(button);
        form.appendChild(actions);
        body.appendChild(form);
        dialog.appendChild(body);

        doc.body.appendChild(backdrop);
        doc.body.appendChild(dialog);
        // The application's own keys (Escape closing modals, shortcuts) never
        // reach it while this dialog is open: stopped at the window, before the
        // document listeners. Default actions (typing, Enter) still happen.
        const win = doc.defaultView;
        if (win && typeof win.addEventListener === 'function') {
            win.addEventListener('keydown', function (event) {
                if (dialog.isConnected) event.stopPropagation();
            }, true);
        }

        function showError(text) {
            error.textContent = text;
            error.hidden = !text;
            const invalid = text ? 'true' : 'false';
            login.setAttribute('aria-invalid', invalid);
            password.setAttribute('aria-invalid', invalid);
        }

        let pending = false;
        function submit() {
            if (pending) return Promise.resolve(null);
            if (!login.value || !password.value) {
                showError('Enter a login and a password.');
                (login.value ? password : login).focus();
                return Promise.resolve(null);
            }
            pending = true;
            button.disabled = true;
            showError('');
            return scope.signIn(login.value, password.value).then(function (result) {
                if (result && result.ok) {
                    reload();
                    return result;
                }
                pending = false;
                button.disabled = false;
                password.value = '';
                showError(messageFor(result));
                password.focus();
                return result;
            });
        }
        form.addEventListener('submit', function (event) {
            event.preventDefault();
            submit();
        });

        login.focus();
        return { dialog: dialog, submit: submit };
    }

    /** Shows the dialog once `scope.resolve()` found nobody signed in. */
    function startSignIn(scope, doc, reload) {
        if (!scope || typeof scope.resolve !== 'function' || typeof scope.needsSignIn !== 'function') {
            return Promise.resolve(false);
        }
        return scope.resolve().then(function () {
            if (!scope.needsSignIn()) return false;
            showSignIn(doc, scope, reload);
            return true;
        }, function () { return false; });
    }

    const api = {
        prksSignInMessage: messageFor,
        prksShowSignIn: showSignIn,
        prksStartSignIn: startSignIn,
    };
    Object.keys(api).forEach(function (k) { root[k] = api[k]; });

    if (root.document && root.prksBrowserScope) {
        const doc = root.document;
        const begin = function () {
            startSignIn(root.prksBrowserScope, doc, function () { root.location.reload(); });
        };
        if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', begin);
        else begin();
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof self !== 'undefined' ? self : typeof window !== 'undefined' ? window : globalThis);
