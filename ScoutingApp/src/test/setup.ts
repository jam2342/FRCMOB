import '@testing-library/jest-dom/vitest';

if (
  typeof window !== 'undefined' &&
  (!window.localStorage || typeof window.localStorage.getItem !== 'function')
) {
  const store = new Map<string, string>();
  Object.defineProperty(window, 'localStorage', {
    value: {
      getItem: (key: string) => (store.has(key) ? store.get(key) || null : null),
      setItem: (key: string, value: string) => {
        store.set(String(key), String(value));
      },
      removeItem: (key: string) => {
        store.delete(String(key));
      },
      clear: () => {
        store.clear();
      },
      key: (index: number) => {
        const keys = [...store.keys()];
        return keys[index] || null;
      },
      get length() {
        return store.size;
      },
    },
    writable: true,
    configurable: true,
  });
}
