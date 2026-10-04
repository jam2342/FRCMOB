import Capacitor
import UIKit
#if FRCMOB_SIMULATOR_TEST && targetEnvironment(simulator)
import WebKit
#endif

class NativeViewController: CAPBridgeViewController {
    override var preferredStatusBarStyle: UIStatusBarStyle { .lightContent }
    override func capacitorDidLoad() {
        bridge?.registerPluginInstance(SecureStoragePlugin())
        #if FRCMOB_SIMULATOR_TEST && targetEnvironment(simulator)
        configureSimulatorNetwork()
        runSimulatorProbe()
        #endif
    }
}

#if FRCMOB_SIMULATOR_TEST && targetEnvironment(simulator)
// Opt-in local verification only. This code is absent from ordinary app builds.
extension NativeViewController {
    private func configureSimulatorNetwork() {
        guard ProcessInfo.processInfo.environment["FRCMOB_SIMULATOR_OFFLINE"] == "1" else { return }
        let script = """
        (() => {
          const original = window.fetch.bind(window);
          const offline = (input, options) => {
            const url = new URL(typeof input === 'string' ? input : input.url, location.href);
            if (url.protocol === 'http:' || url.protocol === 'https:') {
              if (window.__simulatorMockSchedule && url.pathname === '/api/matches/event/2026test/schedule') return Promise.resolve(new Response(JSON.stringify(window.__simulatorMockSchedule), {status: 200, headers: {'Content-Type': 'application/json'}}));
              if (window.__simulatorAllowMockLeave && url.pathname.endsWith('/workspaces/me/leave')) return Promise.resolve(new Response('{}', {status: 200, headers: {'Content-Type': 'application/json'}}));
              return Promise.reject(new TypeError('Simulator offline fixture'));
            }
            return original(input, options);
          };
          Object.defineProperty(window, 'fetch', {get: () => offline, set: () => {}, configurable: false});
          window.__simulatorOffline = true;
        })();
        """
        webView?.configuration.userContentController.addUserScript(WKUserScript(source: script, injectionTime: .atDocumentStart, forMainFrameOnly: true))
    }
    private func runSimulatorProbe() {
        guard let encoded = ProcessInfo.processInfo.environment["FRCMOB_SIMULATOR_PROBE"],
              let data = Data(base64Encoded: encoded),
              let script = String(data: data, encoding: .utf8) else { return }
        Task { @MainActor in
            do {
                guard let webView = self.webView else { return }
                var ready = false
                for _ in 0..<120 {
                    try await Task.sleep(nanoseconds: 250_000_000)
                    let value = try? await webView.evaluateJavaScript("typeof window.Capacitor?.nativePromise === 'function' && document.getElementById('root')?.childElementCount > 0")
                    if (value as? Bool) == true { ready = true; break }
                }
                guard ready else { throw NSError(domain: "SimulatorProbe", code: 1) }
                let result = try await webView.callAsyncJavaScript(script, arguments: [:], in: nil, contentWorld: .page)
                let output = try JSONSerialization.data(withJSONObject: ["result": result ?? NSNull()], options: [.sortedKeys])
                try output.write(to: FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0].appendingPathComponent("native-smoke.json"), options: .atomic)
            } catch {
                let output = try? JSONSerialization.data(withJSONObject: ["error": String(describing: error)], options: [.sortedKeys])
                try? output?.write(to: FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0].appendingPathComponent("native-smoke.json"), options: .atomic)
            }
        }
    }
}
#endif
