// jsdom Storage is a proxy: spying on an instance can silently leave the real
// method running. Node versions using our setup shim need the instance instead.
export const storageMethodTarget = (): Storage =>
  window.localStorage instanceof window.Storage ? window.Storage.prototype : window.localStorage;
