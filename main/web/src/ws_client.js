(function () {
    'use strict';

    var state = {
        ws: null,
        purpose: null,
        onMessage: null,
        onOpen: null,
        onClose: null,
        onError: null,
    };

    function buildWsUrl() {
        var scheme = (window.location.protocol === 'https:') ? 'wss://' : 'ws://';
        return scheme + window.location.host + '/ws';
    }

    function isOpen() {
        return state.ws && state.ws.readyState === WebSocket.OPEN;
    }

    function close() {
        if (!state.ws) return;
        try {
            state.ws.close();
        } catch (_) {
        }
        state.ws = null;
        state.purpose = null;
    }

    function connect(purpose, handlers) {
        handlers = handlers || {};

        if (isOpen() && state.purpose === purpose) {
            state.onMessage = handlers.onMessage || state.onMessage;
            state.onOpen = handlers.onOpen || state.onOpen;
            state.onClose = handlers.onClose || state.onClose;
            state.onError = handlers.onError || state.onError;
            return;
        }

        if (state.ws) {
            close();
        }

        state.purpose = purpose;
        state.onMessage = handlers.onMessage || null;
        state.onOpen = handlers.onOpen || null;
        state.onClose = handlers.onClose || null;
        state.onError = handlers.onError || null;

        var wsUrl = buildWsUrl();
        var ws = new WebSocket(wsUrl);
        state.ws = ws;

        ws.addEventListener('open', function (event) {
            if (purpose === 'terminal') {
                try {
                    ws.send(JSON.stringify({ ws_mode: 'terminal' }));
                } catch (_) {
                }
            }

            if (typeof state.onOpen === 'function') {
                state.onOpen(event);
            }
        });

        ws.addEventListener('message', function (event) {
            if (typeof state.onMessage === 'function') {
                state.onMessage(event);
            }
        });

        ws.addEventListener('close', function (event) {
            if (typeof state.onClose === 'function') {
                state.onClose(event);
            }
            state.ws = null;
            state.purpose = null;
        });

        ws.addEventListener('error', function (event) {
            if (typeof state.onError === 'function') {
                state.onError(event);
            }
        });
    }

    function sendText(text) {
        if (!isOpen()) return false;
        try {
            state.ws.send(text);
            return true;
        } catch (_) {
            return false;
        }
    }

    function sendJson(obj) {
        try {
            return sendText(JSON.stringify(obj));
        } catch (_) {
            return false;
        }
    }

    window.wicanWs = {
        connect: connect,
        close: close,
        isOpen: isOpen,
        sendText: sendText,
        sendJson: sendJson,
        purpose: function () { return state.purpose; },
    };
})();
