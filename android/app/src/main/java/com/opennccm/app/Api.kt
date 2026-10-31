package com.opennccm.app

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayInputStream
import java.security.KeyStore
import java.security.MessageDigest
import java.security.cert.CertificateException
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import java.util.concurrent.TimeUnit
import javax.net.ssl.HostnameVerifier
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManagerFactory
import javax.net.ssl.X509TrustManager

/** El servidor presenta un certificado que no es de una autoridad pública ni uno ya aceptado (instalación con su CA
 *  propia): la app enseña su huella para que el usuario la compare con Mi cuenta › App Android y decida. */
class UntrustedCert(val fingerprint: String, val chain: List<X509Certificate>) : CertificateException("certificado no aceptado")

class ApiError(val code: Int, val error: String, override val message: String) : Exception(message)

fun sha256(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }

fun prettyFp(fp: String): String = fp.uppercase().chunked(2).joinToString(":")

/**
 * Confía en: las autoridades públicas de Android, los certificados aceptados (huella fijada) y los firmados por la
 * autoridad propia de OpenNCCM que se aceptó (así sigue valiendo cuando el instalador renueva el del servidor).
 */
class PinTrust(private val store: Store) : X509TrustManager {
    private val system: X509TrustManager = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm())
        .apply { init(null as KeyStore?) }.trustManagers.filterIsInstance<X509TrustManager>().first()

    fun pinned(chain: Array<out X509Certificate>): Boolean {
        if (chain.any { sha256(it.encoded) in store.pins }) return true
        val pem = store.caPem
        if (pem.isNotEmpty()) {
            return try {
                val ca = CertificateFactory.getInstance("X.509")
                    .generateCertificate(ByteArrayInputStream(pem.toByteArray())) as X509Certificate
                chain[0].checkValidity()
                chain[0].verify(ca.publicKey)
                true
            } catch (e: Exception) {
                false
            }
        }
        return false
    }

    override fun checkServerTrusted(chain: Array<out X509Certificate>, authType: String) {
        try {
            system.checkServerTrusted(chain, authType)
        } catch (e: CertificateException) {
            if (!pinned(chain)) throw UntrustedCert(sha256(chain[0].encoded), chain.toList())
        }
    }

    override fun checkClientTrusted(chain: Array<out X509Certificate>, authType: String) = system.checkClientTrusted(chain, authType)
    override fun getAcceptedIssuers(): Array<X509Certificate> = system.acceptedIssuers
}

class Api(val store: Store) {
    private val trust = PinTrust(store)
    private val client: OkHttpClient = run {
        val ctx = SSLContext.getInstance("TLS").apply { init(null, arrayOf(trust), null) }
        val def = HttpsURLConnection.getDefaultHostnameVerifier()
        // con un certificado aceptado por su huella, el nombre ya no importa (servidores por IP con CA propia)
        val verifier = HostnameVerifier { host, session ->
            def.verify(host, session) || runCatching {
                trust.pinned(session.peerCertificates.filterIsInstance<X509Certificate>().toTypedArray())
            }.getOrDefault(false)
        }
        OkHttpClient.Builder()
            .sslSocketFactory(ctx.socketFactory, trust)
            .hostnameVerifier(verifier)
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(240, TimeUnit.SECONDS)   // «Respaldar ahora» espera al equipo
            .build()
    }

    private fun untrusted(e: Throwable?): UntrustedCert? {
        var c = e
        while (c != null) {
            if (c is UntrustedCert) return c
            c = c.cause
        }
        return null
    }

    /** Petición JSON. Lanza UntrustedCert si hay que aceptar el certificado, ApiError si el servidor responde con error. */
    suspend fun call(method: String, path: String, body: JSONObject? = null, base: String = store.url, auth: Boolean = true): String =
        withContext(Dispatchers.IO) {
            val b = Request.Builder().url(base.trimEnd('/') + path)
            if (auth && store.token.isNotEmpty()) b.header("Authorization", "Bearer " + store.token)
            b.header("User-Agent", "OpenNCCM-Android/" + BuildConfigInfo.version)
            if (method == "POST") b.post((body ?: JSONObject()).toString().toRequestBody("application/json".toMediaType()))
            try {
                client.newCall(b.build()).execute().use { r ->
                    val text = r.body?.string() ?: ""
                    if (!r.isSuccessful) {
                        val j = runCatching { JSONObject(text) }.getOrNull()
                        throw ApiError(r.code, j?.optString("error") ?: "", j?.optString("detail")?.takeIf { it.isNotEmpty() }
                            ?: if (r.code == 401) "La sesión del teléfono ya no es válida. Vuelva a conectar." else "Error ${r.code} del servidor")
                    }
                    text
                }
            } catch (e: ApiError) {
                throw e
            } catch (e: Exception) {
                throw untrusted(e) ?: e
            }
        }

    suspend fun obj(path: String) = JSONObject(call("GET", path))
    suspend fun arr(path: String) = JSONArray(call("GET", path))
    suspend fun post(path: String, body: JSONObject = JSONObject()) = JSONObject(call("POST", path, body))

    /** Acepta el certificado del servidor y, si el servidor publica su autoridad (/ca.crt), también a esta. */
    suspend fun accept(cert: UntrustedCert, base: String) {
        store.pins = store.pins + cert.fingerprint
        runCatching {
            val pem = call("GET", "/ca.crt", base = base, auth = false)
            if (pem.contains("BEGIN CERTIFICATE")) store.caPem = pem
        }
    }

    /** Huella de la autoridad recibida en el QR: se acepta el servidor si su certificado la nombra o coincide. */
    fun pinFromQr(fp: String?, ca: String?) {
        val add = listOfNotNull(fp?.lowercase()?.takeIf { it.length == 64 }, ca?.lowercase()?.takeIf { it.length == 64 })
        if (add.isNotEmpty()) store.pins = store.pins + add
    }
}

object BuildConfigInfo {
    var version: String = "?"
}
