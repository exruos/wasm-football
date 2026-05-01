export function parseId(rawId) {
    const parsed = Number.parseInt(rawId ?? '', 10);
    return Number.isNaN(parsed) ? 0 : parsed;
}