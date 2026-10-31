package com.opennccm.app

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * Lo que la app guarda en el teléfono, cifrado con una clave del almacén de Android: la dirección del servidor, el token
 * del teléfono (nunca la contraseña), las huellas de los certificados en los que se confía y la última copia de las
 * pantallas para verlas sin conexión.
 */
class Store(context: Context) {
    private val prefs: SharedPreferences = EncryptedSharedPreferences.create(
        context,
        "opennccm",
        MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build(),
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
    )

    var url: String
        get() = prefs.getString("url", "") ?: ""
        set(v) = prefs.edit().putString("url", v.trimEnd('/')).apply()

    var token: String
        get() = prefs.getString("token", "") ?: ""
        set(v) = prefs.edit().putString("token", v).apply()

    var user: String
        get() = prefs.getString("user", "") ?: ""
        set(v) = prefs.edit().putString("user", v).apply()

    /** Huellas SHA-256 (hex en minúsculas) de certificados aceptados: el del servidor y el de su autoridad. */
    var pins: Set<String>
        get() = prefs.getStringSet("pins", emptySet()) ?: emptySet()
        set(v) = prefs.edit().putStringSet("pins", v).apply()

    /** Certificado de la autoridad de OpenNCCM (PEM), para seguir confiando cuando se renueve el del servidor. */
    var caPem: String
        get() = prefs.getString("ca", "") ?: ""
        set(v) = prefs.edit().putString("ca", v).apply()

    var lastNotified: Long
        get() = prefs.getLong("last_event", 0L)
        set(v) = prefs.edit().putLong("last_event", v).apply()

    var notify: Boolean
        get() = prefs.getBoolean("notify", true)
        set(v) = prefs.edit().putBoolean("notify", v).apply()

    fun cache(key: String): String? = prefs.getString("c_$key", null)
    fun cache(key: String, json: String) = prefs.edit().putString("c_$key", json).apply()

    val connected: Boolean get() = url.isNotEmpty() && token.isNotEmpty()

    fun logout() {
        val keep = pins
        val ca = caPem
        val u = url
        prefs.edit().clear().apply()
        pins = keep; caPem = ca; url = u
    }
}
