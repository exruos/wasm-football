import { open } from '@spinframework/spin-postgres';
import { get as getVariable } from '@spinframework/spin-variables';
import { parseId } from './parse-id.js';

const DB_URL_DEFAULT = 'postgres://postgres:postgres@localhost:5438/postgres';

export function getConnection() {
    const address = getVariable('db_url') ?? DB_URL_DEFAULT;
    return open(address);
}

export { parseId };
