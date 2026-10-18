// Imported first in main.tsx: modules read localStorage while they load, so the
// stand-in for blocked storage has to be in place before any of them runs.
import { installStorageFallback } from './safeStorage';

installStorageFallback();
