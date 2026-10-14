package com.sastra.app.data

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Secrets (session token, optional local-model API key) encrypted with an
 * AES-256-GCM key that lives in the Android Keystore and never leaves it.
 * Plain settings live in normal SharedPreferences. Nothing secret is in the APK.
 */
class SecureStore(context: Context) {
    private val prefs = context.getSharedPreferences("sastra_secure", Context.MODE_PRIVATE)
    private val settings = context.getSharedPreferences("sastra_settings", Context.MODE_PRIVATE)
    private val alias = "sastra_master_key"

    private fun key(): SecretKey {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (ks.getEntry(alias, null) as? KeyStore.SecretKeyEntry)?.let { return it.secretKey }
        val gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        gen.init(
            KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build()
        )
        return gen.generateKey()
    }

    fun putSecret(name: String, value: String?) {
        if (value == null) { prefs.edit().remove(name).apply(); return }
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.ENCRYPT_MODE, key())
        val blob = c.iv + c.doFinal(value.toByteArray(Charsets.UTF_8))
        prefs.edit().putString(name, Base64.encodeToString(blob, Base64.NO_WRAP)).apply()
    }

    fun getSecret(name: String): String? {
        val raw = prefs.getString(name, null) ?: return null
        return try {
            val blob = Base64.decode(raw, Base64.NO_WRAP)
            val c = Cipher.getInstance("AES/GCM/NoPadding")
            c.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, blob.copyOfRange(0, 12)))
            String(c.doFinal(blob.copyOfRange(12, blob.size)), Charsets.UTF_8)
        } catch (e: Exception) {
            prefs.edit().remove(name).apply() // key was invalidated (e.g. device reset) — force re-login
            null
        }
    }

    fun getString(name: String, def: String = ""): String = settings.getString(name, def) ?: def
    fun putString(name: String, value: String) = settings.edit().putString(name, value).apply()
    fun getBool(name: String, def: Boolean = false): Boolean = settings.getBoolean(name, def)
    fun putBool(name: String, value: Boolean) = settings.edit().putBoolean(name, value).apply()

    fun wipe() {
        prefs.edit().clear().apply()
        settings.edit().clear().apply()
        try {
            KeyStore.getInstance("AndroidKeyStore").apply { load(null) }.deleteEntry(alias)
        } catch (_: Exception) {
        }
    }
}
