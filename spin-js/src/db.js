import { open } from '@spinframework/spin-postgres';
import { get as getVariable } from '@spinframework/spin-variables';

const DB_URL_DEFAULT = 'postgres://postgres:postgres@localhost:5438/postgres';

export function getConnection() {
    const address = getVariable('db_url') ?? DB_URL_DEFAULT;
    return open(address);
}

export function parseId(rawId) {
    const parsed = Number.parseInt(rawId ?? '', 10);
    return Number.isNaN(parsed) ? 0 : parsed;
}
