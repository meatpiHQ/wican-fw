(function () {
    'use strict';

	var activeTermType = 'console';

    function $(id) {
        return document.getElementById(id);
    }

    function appendOut(text) {
        var out = $('terminal_output');
        if (!out) return;
        var s = (text == null) ? '' : String(text);
        // Normalize line endings. ELM327 output is commonly CR-only, sometimes CRLF.
        s = s.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
        // Drop other ASCII control chars except tab/newline.
        s = s.replace(/[\x00-\x08\x0B\x0C\x0E-\x1F]/g, '');
        out.textContent += s;
        out.scrollTop = out.scrollHeight;
    }

    function ensureElmPrompt() {
        var out = $('terminal_output');
        if (!out) return;
        var t = out.textContent || '';
        if (t.length === 0) {
            appendOut('>');
            return;
        }
        // If the last visible character isn't a prompt or newline, start a new line.
        var last = t[t.length - 1];
        if (last !== '>' && last !== '\n') {
            appendOut('\n');
        }
        // If we are at a line start, show a prompt.
        t = out.textContent || '';
        last = t[t.length - 1];
        if (last === '\n') {
            appendOut('>');
        }
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
				if (msg && msg.type === 'ws_mode') {
					// Firmware ack; not terminal output.
					return;
				}
				// Any other JSON messages are not terminal output.
				return;
            } catch (_) {
            }
        }


		// For protocols that stream plain text (e.g. ELM327), just show it.
		appendOut(data);
    }

    function connectClicked() {
        var typeSel = $('terminal_type');
        var termType = typeSel ? typeSel.value : 'console';
		activeTermType = termType;

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
				// Select terminal behavior on firmware side.
				try { window.wicanWs.sendJson({ ws_mode: 'terminal', terminal_type: termType }); } catch (_) {}
				if (termType === 'elm327') {
					ensureElmPrompt();
				}
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

        if (activeTermType === 'elm327') {
            // Send raw, CR-terminated ELM commands so firmware can treat it like the TCP port.
			// Also print a PuTTY-like transcript line even if ELM echo is off (ATE0).
			ensureElmPrompt();
			appendOut(cmd + '\n');
            window.wicanWs.sendText(cmd + '\r');
        } else {
			appendOut(cmd + '\n');
            window.wicanWs.sendJson({ cmd: cmd });
        }
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
