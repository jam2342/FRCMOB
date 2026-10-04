import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'app.frcmob.scouting',
  appName: 'FRCMOB',
  webDir: 'dist-native',
  // Bridge debug logs can include request headers and credential arguments.
  loggingBehavior: 'none',
  backgroundColor: '#292929',
  // Ship local files, including both detector models. No remote website shell.
  server: { androidScheme: 'https', iosScheme: 'capacitor' },
  ios: { contentInset: 'automatic', preferredContentMode: 'mobile' },
  plugins: {
    SystemBars: { style: 'DARK', insetsHandling: 'css', initialViewportFitValueHint: 'cover' },
    // Keep the existing fetch/queue API while using native HTTP outside the WebView's CORS boundary.
    CapacitorHttp: { enabled: true },
  },
};
export default config;
