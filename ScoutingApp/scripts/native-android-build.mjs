import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { nativeTools } from './native-tools.mjs';
const { sdk, jdk } = nativeTools();
if (!sdk || !existsSync(`${sdk}/platforms/android-36/android.jar`)) throw new Error('Android SDK platform 36 is missing. Run npm run native:doctor.');
for (const arg of process.argv.slice(2)) if (!['--release', '--signed'].includes(arg)) throw new Error(`Unknown argument: ${arg}`);
const release = process.argv.includes('--release');
const signed = process.argv.includes('--signed');
if (signed && !release) throw new Error('--signed requires --release.');
const signingNames = ['FRCMOB_UPLOAD_KEYSTORE','FRCMOB_UPLOAD_STORE_PASSWORD','FRCMOB_UPLOAD_KEY_ALIAS','FRCMOB_UPLOAD_KEY_PASSWORD'];
const signingCount = signingNames.filter(name => process.env[name]?.trim()).length;
if (signingCount && signingCount !== signingNames.length) throw new Error('Incomplete upload signing environment. Supply all four FRCMOB_UPLOAD_* variables or unset them all.');
if (signed && !signingCount) throw new Error('Signed release requires the owner-provided upload keystore and all four FRCMOB_UPLOAD_* variables.');
if (signingCount && !existsSync(process.env.FRCMOB_UPLOAD_KEYSTORE)) throw new Error('Upload keystore does not exist.');
const result = spawnSync('./gradlew', [...(release ? ['bundleRelease', 'lintRelease'] : ['assembleDebug', 'lintDebug']), '--console=plain'], {
  cwd: new URL('../android/', import.meta.url), stdio: 'inherit',
  env: { ...process.env, ANDROID_HOME: sdk, ...(jdk ? { JAVA_HOME: jdk } : {}) },
});
if (result.error) throw result.error;
process.exitCode = result.status ?? 1;

if (release && result.status === 0) console.log(signingCount ? 'Release bundle built with owner upload signing. Verify the signature before uploading.' : 'Unsigned release bundle built. Owner signing and store setup are still required.');
