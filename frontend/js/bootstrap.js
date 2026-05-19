/**
 * Load ``/api/lab-layout`` before the rest of the app so mm↔px and danger radius match backend.
 */
import { applyLabLayoutFromApiDoc } from './config.js';

async function start() {
    const r = await fetch('/api/lab-layout');
    if (!r.ok) {
        throw new Error(`HTTP ${r.status}`);
    }
    applyLabLayoutFromApiDoc(await r.json());
    const buildV =
        new URL(import.meta.url).searchParams.get('v') ?? String(Date.now());
    await import(`./app-main.js?v=${buildV}`);
}

start().catch((e) => {
    console.error(e);
    document.body.insertAdjacentHTML(
        'beforeend',
        `<pre style="padding:2rem;color:#fca5a5;background:#450a0a;font-family:ui-monospace,monospace;margin:2rem;border-radius:8px">`
            + `<strong>Failed to load lab layout.</strong>\n`
            + `Set LAB_VIEW_PATH in .env (lab view bundle with lab_manifest.json and layout.json).\n\n`
            + `${e}</pre>`,
    );
});
