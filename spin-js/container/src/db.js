import postgres from 'postgres';
import { parseId } from '../../src/parse-id.js';

const DB_URL_DEFAULT = 'postgres://postgres:postgres@localhost:5438/postgres';

let sqlClient;

function getSqlClient() {
    if (sqlClient) {
        return sqlClient;
    }

    const connectionString = process.env.DB_URL ?? DB_URL_DEFAULT;
    sqlClient = postgres(connectionString);
    return sqlClient;
}

export function getConnection() {
    const sql = getSqlClient();

    return {
        async query(text, params = []) {
            const rows = await sql.unsafe(text, params);
            return { rows };
        },
    };
}

export { parseId };