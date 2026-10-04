package app.frcmob.scouting;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/** Device-bound AES-GCM key; only authenticated ciphertext reaches app storage. */
@CapacitorPlugin(name = "SecureStorage")
public class SecureStoragePlugin extends Plugin {
    private static final String ALIAS = "frcmob.secure.v1";
    private synchronized SecretKey key() throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        if (store.containsAlias(ALIAS)) return (SecretKey) store.getKey(ALIAS, null);
        KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
        generator.init(new KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setKeySize(256).build());
        return generator.generateKey();
    }
    private SharedPreferences storage() {
        return getContext().getSharedPreferences("frcmob_secure_v1", Context.MODE_PRIVATE);
    }
    private String encryptValue(String value) throws Exception {
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, key());
        return Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP) + "." +
            Base64.encodeToString(cipher.doFinal(value.getBytes(StandardCharsets.UTF_8)), Base64.NO_WRAP);
    }
    private String decryptValue(String value) throws Exception {
        String[] parts = value.split("\\.", -1);
        if (parts.length != 2) throw new IllegalArgumentException();
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(128, Base64.decode(parts[0], Base64.NO_WRAP)));
        return new String(cipher.doFinal(Base64.decode(parts[1], Base64.NO_WRAP)), StandardCharsets.UTF_8);
    }
    private String required(PluginCall call, String field) {
        String value = call.getString(field);
        if (value == null) throw new IllegalArgumentException();
        return value;
    }
    @PluginMethod public void get(PluginCall call) {
        try {
            String value = storage().getString(required(call, "key"), null);
            JSObject result = new JSObject();
            result.put("value", value == null ? org.json.JSONObject.NULL : decryptValue(value));
            call.resolve(result);
        } catch (Exception error) { call.reject("Secure storage could not be read."); }
    }
    @PluginMethod public void set(PluginCall call) {
        try {
            boolean saved = storage().edit().putString(required(call, "key"), encryptValue(required(call, "value"))).commit();
            if (!saved) throw new IllegalStateException();
            call.resolve();
        } catch (Exception error) { call.reject("Secure storage could not be saved."); }
    }
    @PluginMethod public void remove(PluginCall call) {
        try {
            if (!storage().edit().remove(required(call, "key")).commit()) throw new IllegalStateException();
            call.resolve();
        } catch (Exception error) { call.reject("Secure storage could not be cleared."); }
    }
    @PluginMethod public void encrypt(PluginCall call) {
        try { call.resolve(new JSObject().put("value", encryptValue(required(call, "value")))); }
        catch (Exception error) { call.reject("Saved changes could not be protected."); }
    }
    @PluginMethod public void decrypt(PluginCall call) {
        try { call.resolve(new JSObject().put("value", decryptValue(required(call, "value")))); }
        catch (Exception error) { call.reject("Protected changes could not be read."); }
    }
}
