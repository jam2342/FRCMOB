import { registerPlugin } from '@capacitor/core';

interface SecureStoragePlugin {
  get(options: { key: string }): Promise<{ value: string | null }>;
  set(options: { key: string; value: string }): Promise<void>;
  remove(options: { key: string }): Promise<void>;
  encrypt(options: { value: string }): Promise<{ value: string }>;
  decrypt(options: { value: string }): Promise<{ value: string }>;
}

// No web fallback: native credential persistence must fail closed.
export const SecureStorage = registerPlugin<SecureStoragePlugin>('SecureStorage');
