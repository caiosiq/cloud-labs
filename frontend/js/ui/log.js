/**
 * Append a single timestamped line to the in-page log panel (#log-output).
 * @param {string} message
 * @param {'info'|'warn'|'error'|string} [type='info'] CSS class suffix for color.
 */
export function log(message, type = 'info') {
    const logOutput = document.getElementById('log-output');
    if (!logOutput) return;
    const entry = document.createElement('div');
    entry.className = `log-entry ${type}`;
    entry.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
    logOutput.prepend(entry);
    if (logOutput.children.length > 50) logOutput.removeChild(logOutput.lastChild);
}
