import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';
const root = fileURLToPath(new URL('../', import.meta.url));
if (process.platform !== 'darwin') throw new Error('iOS builds require macOS and full Xcode.');
for (const arg of process.argv.slice(2)) if (!['--probe', '--archive'].includes(arg)) throw new Error(`Unknown iOS build argument: ${arg}`);
const probe = process.argv.includes('--probe');
const archive = process.argv.includes('--archive');
if (probe && archive) throw new Error('Probe hooks are forbidden in device archives.');
const args = ['-project', 'ios/App/App.xcodeproj', '-scheme', 'App', '-destination', 'generic/platform=iOS Simulator', '-derivedDataPath', 'ios/DerivedData', 'build'];
if (archive) { args.splice(0, args.length, '-project', 'ios/App/App.xcodeproj', '-scheme', 'App', '-configuration', 'Release', '-destination', 'generic/platform=iOS', '-derivedDataPath', 'ios/DerivedData', '-archivePath', 'ios/archives/FRCMOB.xcarchive', 'archive', 'CODE_SIGNING_ALLOWED=NO'); }
if (probe) args.push('SWIFT_ACTIVE_COMPILATION_CONDITIONS=DEBUG FRCMOB_SIMULATOR_TEST');
// Keep Xcode's simulator ad-hoc signing: Keychain fails in unsigned simulator apps.
const result = spawnSync('xcodebuild', args, {cwd: root, stdio: 'inherit'});
if (result.error) throw result.error;
if (result.status !== 0) process.exit(result.status ?? 1);
if (archive) { console.log('Unsigned device archive: ios/archives/FRCMOB.xcarchive. Owner signing is required before export or TestFlight.'); process.exit(0); }
console.log(`Simulator app: ${join(root, 'ios/DerivedData/Build/Products/Debug-iphonesimulator/App.app')}`);
console.log(probe ? 'Simulator test hook enabled; rebuild normally before distributing.' : 'Normal simulator build; test hook excluded.');
