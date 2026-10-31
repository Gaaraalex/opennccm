package com.opennccm.app

import android.Manifest
import android.os.Build
import android.os.Bundle
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.biometric.BiometricManager
import androidx.biometric.BiometricManager.Authenticators.BIOMETRIC_WEAK
import androidx.biometric.BiometricManager.Authenticators.DEVICE_CREDENTIAL
import androidx.biometric.BiometricPrompt
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentActivity
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume

class MainActivity : FragmentActivity() {
    private var scanDone: ((String?) -> Unit)? = null
    private val scanner = registerForActivityResult(ScanContract()) { r -> scanDone?.invoke(r.contents); scanDone = null }
    private val notifPerm = registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        BuildConfigInfo.version = runCatching { packageManager.getPackageInfo(packageName, 0).versionName }.getOrNull() ?: "?"
        Notify.channels(this)
        setContent { NccmTheme { App(this) } }
    }

    /** Escanea un QR (vincular con el servidor o etiqueta de un equipo) y devuelve su texto, o null si se cancela. */
    fun scan(prompt: String, done: (String?) -> Unit) {
        scanDone = done
        scanner.launch(ScanOptions().setDesiredBarcodeFormats(ScanOptions.QR_CODE).setPrompt(prompt)
            .setBeepEnabled(false).setOrientationLocked(false))
    }

    fun askNotifications() {
        if (Build.VERSION.SDK_INT >= 33) notifPerm.launch(Manifest.permission.POST_NOTIFICATIONS)
    }

    /**
     * Huella (o el bloqueo de pantalla) antes de cualquier acción que cambie algo: aprobar, rechazar, respaldar,
     * reconocer. Si el teléfono no tiene ni huella ni bloqueo, se acepta (la app ya pidió confirmación en un diálogo).
     */
    suspend fun confirmIdentity(title: String, subtitle: String): Boolean {
        val bm = BiometricManager.from(this)
        if (bm.canAuthenticate(BIOMETRIC_WEAK or DEVICE_CREDENTIAL) != BiometricManager.BIOMETRIC_SUCCESS) return true
        return suspendCancellableCoroutine { cont ->
            val prompt = BiometricPrompt(this, ContextCompat.getMainExecutor(this), object : BiometricPrompt.AuthenticationCallback() {
                override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) {
                    if (cont.isActive) cont.resume(true)
                }

                override fun onAuthenticationError(errorCode: Int, errString: CharSequence) {
                    if (cont.isActive) cont.resume(false)
                }
            })
            prompt.authenticate(BiometricPrompt.PromptInfo.Builder()
                .setTitle(title).setSubtitle(subtitle)
                .setAllowedAuthenticators(BIOMETRIC_WEAK or DEVICE_CREDENTIAL)
                .build())
        }
    }
}
