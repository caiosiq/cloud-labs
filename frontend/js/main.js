/** Entry script — version query propagates to bootstrap → app-main for cache busting. */
const _buildV =
    new URL(import.meta.url).searchParams.get('v') ?? String(Date.now());
await import(`./bootstrap.js?v=${_buildV}`);
