import { existsSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

export function nativeTools() {
  const sdk = process.env.ANDROID_HOME || process.env.ANDROID_SDK_ROOT || [
    join(homedir(), 'Library/Android/sdk'),
    join(homedir(), 'Library/Caches/frcmob-native/android-sdk'),
  ].find(existsSync);
  const jdk = process.env.JAVA_HOME || [
    '/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home',
    '/usr/local/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home',
  ].find(existsSync);
  return { sdk, jdk, java: jdk ? join(jdk, 'bin/java') : 'java' };
}
