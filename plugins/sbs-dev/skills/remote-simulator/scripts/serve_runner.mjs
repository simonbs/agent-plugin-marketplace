// Keep a session identity in the OS command line, including during startup.
import { pathToFileURL } from 'node:url';
const args = process.argv.slice(2);
const option = (name) => args[args.indexOf(name) + 1];
const entry = option('--serve');
if (!entry || !option('--session') || !option('--device') || !option('--port')) {
  throw new Error('Missing session, serve entry, device, or port');
}
process.argv = [process.execPath, entry, '--port', option('--port'),
  '--host', '127.0.0.1', '--codec', 'mjpeg', option('--device')];
await import(pathToFileURL(entry).href);
