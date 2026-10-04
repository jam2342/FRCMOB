import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { nativeTools } from './native-tools.mjs';
const { sdk, java } = nativeTools();
const javaResult = spawnSync(java, ['-version'], { encoding: 'utf8' });
const javaText = javaResult.stderr || javaResult.stdout || '';
const xcode = spawnSync('xcodebuild', ['-version'], { encoding: 'utf8' });
const ios = xcode.status === 0;
const android = Boolean(sdk && existsSync(join(sdk, 'platforms/android-36/android.jar')) && existsSync(join(sdk, 'build-tools/36.0.0/aapt2')) && /version "21\./.test(javaText));
console.log(JSON.stringify({
  ios: { ready: ios, detail: ios ? xcode.stdout.trim() : (xcode.stderr || 'Xcode not installed').trim() },
  android: { ready: android, sdk: sdk || null, java: javaText.trim(), needs: android ? [] : ['Android SDK platform 36 and build-tools 36.0.0', 'JDK 21 (JAVA_HOME)'] },
}, null, 2));
if (!ios || !android) process.exitCode = 1;
