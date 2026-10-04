import Capacitor
import Security
import CryptoKit

@objc(SecureStoragePlugin)
public class SecureStoragePlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "SecureStoragePlugin"
    public let jsName = "SecureStorage"
    public let pluginMethods: [CAPPluginMethod] = ["get", "set", "remove", "encrypt", "decrypt"].map {
        CAPPluginMethod(name: $0, returnType: CAPPluginReturnPromise)
    }
    private let service = "app.frcmob.scouting.secure.v1"
    private enum StorageError: Error { case failed }
    private func query(_ key: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
         kSecAttrAccount as String: key, kSecAttrSynchronizable as String: false]
    }
    private func read(_ key: String) throws -> Data? {
        var request = query(key)
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(request as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = result as? Data else { throw StorageError.failed }
        return data
    }
    private func write(_ key: String, _ data: Data) throws {
        let attributes: [String: Any] = [kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly]
        let status = SecItemUpdate(query(key) as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            guard SecItemAdd(query(key).merging(attributes) { _, new in new } as CFDictionary, nil) == errSecSuccess else { throw StorageError.failed }
        } else if status != errSecSuccess { throw StorageError.failed }
    }
    private func key() throws -> SymmetricKey {
        if let data = try read("queue-encryption-key") { return SymmetricKey(data: data) }
        let key = SymmetricKey(size: .bits256)
        try write("queue-encryption-key", key.withUnsafeBytes { Data($0) })
        return key
    }
    private func required(_ call: CAPPluginCall, _ name: String) throws -> String {
        guard let value = call.getString(name) else { throw StorageError.failed }
        return value
    }
    @objc func get(_ call: CAPPluginCall) {
        do {
            let data = try read(required(call, "key"))
            if let data = data {
                guard let value = String(data: data, encoding: .utf8) else { throw StorageError.failed }
                call.resolve(["value": value])
            } else { call.resolve(["value": NSNull()]) }
        } catch { call.reject("Secure storage could not be read.") }
    }
    @objc func set(_ call: CAPPluginCall) {
        do { try write(required(call, "key"), Data(required(call, "value").utf8)); call.resolve() }
        catch { call.reject("Secure storage could not be saved.") }
    }
    @objc func remove(_ call: CAPPluginCall) {
        do {
            let status = SecItemDelete(try query(required(call, "key")) as CFDictionary)
            guard status == errSecSuccess || status == errSecItemNotFound else { throw StorageError.failed }
            call.resolve()
        } catch { call.reject("Secure storage could not be cleared.") }
    }
    @objc func encrypt(_ call: CAPPluginCall) {
        do {
            let box = try AES.GCM.seal(Data(required(call, "value").utf8), using: key())
            guard let data = box.combined else { throw StorageError.failed }
            call.resolve(["value": data.base64EncodedString()])
        } catch { call.reject("Saved changes could not be protected.") }
    }
    @objc func decrypt(_ call: CAPPluginCall) {
        do {
            guard let data = Data(base64Encoded: try required(call, "value")) else { throw StorageError.failed }
            let clear = try AES.GCM.open(AES.GCM.SealedBox(combined: data), using: key())
            guard let value = String(data: clear, encoding: .utf8) else { throw StorageError.failed }
            call.resolve(["value": value])
        } catch { call.reject("Protected changes could not be read.") }
    }
}
