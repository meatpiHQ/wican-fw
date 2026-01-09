(function () {
    'use strict';

    function $(id) {
        return document.getElementById(id);
    }

    function appendOut(text) {
        var out = $('terminal_output');
        if (!out) return;
        out.textContent += text;
        out.scrollTop = out.scrollHeight;
    }

    function setConnectedUi(connected) {
        var btn = $('terminal_connect_btn');
        var status = $('terminal_status_badge');
        var sendBtn = $('terminal_send_btn');
        var input = $('terminal_input');
        var typeSel = $('terminal_type');

        if (btn) btn.textContent = connected ? 'Disconnect' : 'Connect';
        if (status) {
            status.textContent = connected ? 'Connected' : 'Disconnected';
            status.classList.remove('status-connected');
            status.classList.remove('status-disconnected');
            status.classList.add(connected ? 'status-connected' : 'status-disconnected');
        }
        if (sendBtn) sendBtn.disabled = !connected;
        if (input) input.disabled = !connected;
		if (typeSel) typeSel.disabled = connected;
    }

    function isConnected() {
        return window.wicanWs && window.wicanWs.isOpen() && window.wicanWs.purpose() === 'terminal';
    }

    function handleWsMessage(event) {
        var data = event.data;
        if (typeof data !== 'string') return;

        if (data.length > 0 && data[0] === '{') {
            try {
                var msg = JSON.parse(data);
                if (msg && msg.type === 'term_out' && typeof msg.data === 'string') {
                    appendOut(msg.data);
                    return;
                }
            } catch (_) {
            }
        }

        // Unknown payload (ignore for now)
    }

    function connectClicked() {
        var typeSel = $('terminal_type');
        var termType = typeSel ? typeSel.value : 'console';

        if (termType !== 'console') {
            alert('Only "Console command line" is available for now.');
            return;
        }

        if (!window.wicanWs) {
            alert('WebSocket client is not loaded.');
            return;
        }

        if (isConnected()) {
            window.wicanWs.close();
            setConnectedUi(false);
            appendOut('\n[disconnected]\n');
            return;
        }

        window.wicanWs.connect('terminal', {
            onOpen: function () {
                setConnectedUi(true);
                appendOut('[connected]\n');
            },
            onClose: function () {
                setConnectedUi(false);
                appendOut('\n[disconnected]\n');
            },
            onError: function () {
                setConnectedUi(false);
            },
            onMessage: handleWsMessage,
        });
    }

    function sendLine() {
        var input = $('terminal_input');
        if (!input) return;

        if (!isConnected()) {
            setConnectedUi(false);
            appendOut('\n[disconnected]\n');
            return;
        }

        var cmd = (input.value || '').trim();
        if (cmd.length === 0) return;

        appendOut(cmd + '\n');
        window.wicanWs.sendJson({ cmd: cmd });
        input.value = '';
    }

    function initTerminalUi() {
        var btn = $('terminal_connect_btn');
        if (btn) btn.addEventListener('click', connectClicked);

        var sendBtn = $('terminal_send_btn');
        if (sendBtn) sendBtn.addEventListener('click', sendLine);

        var input = $('terminal_input');
        if (input) {
            input.addEventListener('keydown', function (e) {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    sendLine();
                }
            });
        }

        setConnectedUi(isConnected());
    }

    window.addEventListener('load', initTerminalUi);
})();
