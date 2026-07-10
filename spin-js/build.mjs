import { build } from 'esbuild';
import { SpinEsbuildPlugin } from "@spinframework/build-tools/plugins/esbuild";

const debug = process.argv.includes('--debug');

await build({
    entryPoints: ['./src/index.js'],
    outfile: './build/bundle.js',
    bundle: true,
    format: 'esm',
    platform: 'browser',
    sourcemap: debug,
    minify: !debug,
    resolveExtensions: ['.js'],
    plugins: [await SpinEsbuildPlugin({
        componentize: {
            debug,
            enableAot: !debug,
            output: './target/football-js.wasm',
            initLocation: 'http://football-js.localhost',
        }
    })],
});
