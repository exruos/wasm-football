export function parseId(rawId) {
    const parsed = Number.parseInt(rawId ?? '', 10);
    if (Number.isNaN(parsed)) {
        const error = new Error('Invalid id');
        error.status = 400;
        throw error;
    }
    return parsed;
}