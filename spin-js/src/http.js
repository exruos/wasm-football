export function jsonResponse(payload, status = 200) {
    return new Response(JSON.stringify(payload), {
        status,
        headers: {
            'content-type': 'application/json',
        },
    });
}

export function textResponse(message, status) {
    return new Response(message, { status });
}

export function notFound() {
    return new Response(null, { status: 404 });
}
