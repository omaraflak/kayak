import React, { useEffect, useRef } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';

/**
 * A real terminal into the conversation's container.
 *
 * xterm.js on this side, a WebSocket in the middle, and a `docker exec` with a
 * proper PTY on the other end -- so line editing, colors, tab completion,
 * Ctrl-C, and full-screen programs all behave like a normal shell. Keystrokes
 * go over the socket as-is; `{"type":"resize"}` control frames keep the PTY the
 * same size as the rendered terminal.
 */

interface ContainerTerminalProps {
  conversationId: string;
}

export const ContainerTerminal: React.FC<ContainerTerminalProps> = ({ conversationId }) => {
  const hostRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    const term = new Terminal({
      fontFamily: 'ui-monospace, Menlo, monospace',
      fontSize: 12,
      cursorBlink: true,
      theme: { background: '#0a0c0f', foreground: '#e6e8ec' },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(host);
    fit.fit();

    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const socket = new WebSocket(
      `${protocol}://${window.location.host}/api/conversations/${conversationId}/terminal`
    );
    socket.binaryType = 'arraybuffer';

    const sendResize = () => {
      if (socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }));
      }
    };

    socket.onopen = () => {
      sendResize();
      term.focus();
    };

    socket.onmessage = (event) => {
      if (typeof event.data === 'string') {
        // Text frames only carry pre-stream errors ("Docker is not running");
        // everything the shell itself says arrives as binary.
        try {
          const message = JSON.parse(event.data);
          if (message?.type === 'error') {
            term.write(`\x1b[33m${message.data}\x1b[0m\r\n`);
            return;
          }
        } catch {
          /* not a control frame; fall through and render it */
        }
        term.write(event.data);
        return;
      }
      term.write(new Uint8Array(event.data));
    };

    socket.onclose = () => {
      term.write('\r\n\x1b[90m[session ended — reopen the terminal to reconnect]\x1b[0m\r\n');
    };

    const keystrokes = term.onData((chunk) => {
      if (socket.readyState === WebSocket.OPEN) socket.send(chunk);
    });

    // The panel can be resized with the window; the PTY must follow, or programs
    // like editors wrap at the wrong column.
    const observer = new ResizeObserver(() => {
      fit.fit();
      sendResize();
    });
    observer.observe(host);

    return () => {
      observer.disconnect();
      keystrokes.dispose();
      socket.close();
      term.dispose();
    };
  }, [conversationId]);

  return <div ref={hostRef} className="h-full w-full bg-[#0a0c0f] [&_.xterm]:h-full p-2" />;
};
