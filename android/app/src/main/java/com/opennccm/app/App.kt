@file:OptIn(ExperimentalMaterial3Api::class)

package com.opennccm.app

import android.content.Intent
import android.net.Uri
import android.os.Build
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.List
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Check
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch
import org.json.JSONObject

/** Pantallas apiladas encima de las pestañas (atrás vuelve a la anterior). */
sealed class Screen {
    data class Device(val id: Int) : Screen()
    data class Version(val id: Int, val title: String) : Screen()
    data class Change(val id: Int) : Screen()
}

/** Estado común de la app: almacén cifrado, API, mensajes y navegación. */
class Ctx(val act: MainActivity, val store: Store, val api: Api, val scope: CoroutineScope, val snack: SnackbarHostState) {
    var connected by mutableStateOf(store.connected)
    var role by mutableStateOf(runCatching { JSONObject(store.user).optString("role") }.getOrDefault("viewer"))
    var tab by mutableStateOf(0)
    val stack = mutableStateListOf<Screen>()
    var pendingCert by mutableStateOf<Pair<UntrustedCert, suspend () -> Unit>?>(null)

    val canRun get() = role == "operator" || role == "admin"
    val isAdmin get() = role == "admin"

    fun open(s: Screen) { stack.add(s) }
    fun toast(msg: String) { scope.launch { snack.showSnackbar(msg) } }

    /** Ejecuta una llamada; si el servidor presenta un certificado nuevo, pide aceptarlo y la repite. */
    fun run(block: suspend () -> Unit) {
        scope.launch {
            try {
                block()
            } catch (e: UntrustedCert) {
                pendingCert = e to block
            } catch (e: ApiError) {
                if (e.code == 401 && connected) {
                    store.logout(); connected = false
                }
                toast(e.message)
            } catch (e: Exception) {
                toast("Sin conexión con el servidor: " + (e.message ?: e.javaClass.simpleName))
            }
        }
    }

    fun signedIn(r: JSONObject, url: String) {
        store.url = url
        store.token = r.getString("token")
        store.user = r.getJSONObject("user").toString()
        role = r.getJSONObject("user").optString("role")
        connected = true
        tab = 0
        run {
            store.lastNotified = api.obj("/api/mobile/notifications?since=0").optLong("last")
            Notify.schedule(act, store.notify)
        }
        act.askNotifications()
    }

    /** QR de una etiqueta de equipo: abre su ficha (si es de este servidor). */
    fun openLabel(text: String?) {
        val j = runCatching { JSONObject(text ?: "") }.getOrNull() ?: return toast("Ese QR no es de OpenNCCM")
        if (j.has("device")) open(Screen.Device(j.getInt("device"))) else toast("Ese QR es para vincular el teléfono, no de un equipo")
    }
}

@Composable
fun App(act: MainActivity) {
    val scope = rememberCoroutineScope()
    val snack = remember { SnackbarHostState() }
    val ctx = remember {
        val store = Store(act)
        Ctx(act, store, Api(store), scope, snack)
    }
    Scaffold(snackbarHost = { SnackbarHost(snack) }) { pad ->
        Box(Modifier.padding(pad).fillMaxSize()) {
            if (!ctx.connected) ConnectScreen(ctx) else MainScreen(ctx)
        }
    }
    ctx.pendingCert?.let { (cert, retry) ->
        AlertDialog(
            onDismissRequest = { ctx.pendingCert = null },
            title = { Text("Certificado del servidor") },
            text = {
                Column {
                    Text("El servidor usa un certificado propio de OpenNCCM. Compruebe que esta huella es la que aparece en Mi cuenta › App Android:")
                    Spacer(Modifier.height(10.dp))
                    Text(prettyFp(cert.fingerprint), fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.bodySmall)
                }
            },
            confirmButton = {
                Button(onClick = {
                    ctx.pendingCert = null
                    ctx.run { ctx.api.accept(cert, if (ctx.connected) ctx.store.url else lastBase); retry() }
                }) { Text("Confiar") }
            },
            dismissButton = { TextButton(onClick = { ctx.pendingCert = null }) { Text("Cancelar") } },
        )
    }
}

private var lastBase = ""

fun normUrl(u: String): String {
    var s = u.trim().trimEnd('/')
    if (s.isEmpty()) return s
    if (!s.startsWith("http://") && !s.startsWith("https://")) s = "https://$s"
    return s
}

@Composable
fun ConnectScreen(ctx: Ctx) {
    var url by remember { mutableStateOf(ctx.store.url.ifEmpty { "https://" }) }
    var user by remember { mutableStateOf("") }
    var pass by remember { mutableStateOf("") }
    var otp by remember { mutableStateOf("") }
    var needOtp by remember { mutableStateOf(false) }
    var code by remember { mutableStateOf("") }
    var withCode by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    val device = (Build.MANUFACTURER + " " + Build.MODEL).trim()

    fun login() {
        busy = true
        val base = normUrl(url)
        lastBase = base
        ctx.run {
            try {
                val body = JSONObject().put("username", user).put("password", pass).put("otp", otp).put("device", device)
                val r = JSONObject(ctx.api.call("POST", "/api/mobile/login", body, base = base, auth = false))
                ctx.signedIn(r, base)
            } catch (e: ApiError) {
                if (e.error == "otp_required") needOtp = true else throw e
            } finally {
                busy = false
            }
        }
    }

    fun pair(base: String, c: String) {
        busy = true
        lastBase = base
        ctx.run {
            try {
                val body = JSONObject().put("code", c).put("device", device)
                val r = JSONObject(ctx.api.call("POST", "/api/mobile/pair", body, base = base, auth = false))
                ctx.signedIn(r, base)
            } finally {
                busy = false
            }
        }
    }

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(24.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("OpenNCCM", style = MaterialTheme.typography.headlineMedium)
        Text("Conecte el teléfono con su servidor de OpenNCCM.", color = C.muted)
        Button(onClick = {
            ctx.act.scan("Escanee el QR de Mi cuenta › App Android") { text ->
                val j = runCatching { JSONObject(text ?: "") }.getOrNull()
                if (j == null || !j.has("code")) {
                    if (text != null) ctx.toast("Ese QR no es el de vincular de OpenNCCM")
                } else {
                    ctx.api.pinFromQr(j.optString("fp"), j.optString("ca"))
                    url = j.getString("url")
                    pair(normUrl(j.getString("url")), j.getString("code"))
                }
            }
        }, modifier = Modifier.fillMaxWidth(), enabled = !busy) { Text("Vincular con el QR del servidor") }
        Text("o con la dirección del servidor y su usuario:", color = C.muted, style = MaterialTheme.typography.bodySmall)
        OutlinedTextField(url, { url = it }, label = { Text("Dirección (IP o nombre)") }, singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri), modifier = Modifier.fillMaxWidth())
        if (withCode) {
            OutlinedTextField(code, { code = it.trim() }, label = { Text("Código de vinculación") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            Button(onClick = { pair(normUrl(url), code) }, enabled = !busy && code.length > 10, modifier = Modifier.fillMaxWidth()) { Text("Vincular") }
        } else {
            OutlinedTextField(user, { user = it }, label = { Text("Usuario") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(pass, { pass = it }, label = { Text("Contraseña") }, singleLine = true,
                visualTransformation = PasswordVisualTransformation(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password), modifier = Modifier.fillMaxWidth())
            if (needOtp) OutlinedTextField(otp, { otp = it.filter(Char::isDigit).take(8) }, label = { Text("Código de verificación en dos pasos") },
                singleLine = true, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword), modifier = Modifier.fillMaxWidth())
            Button(onClick = { login() }, enabled = !busy && user.isNotBlank() && pass.isNotEmpty() && url.length > 8,
                modifier = Modifier.fillMaxWidth()) { Text(if (busy) "Conectando…" else "Entrar") }
        }
        TextButton(onClick = { withCode = !withCode }) {
            Text(if (withCode) "Entrar con usuario y contraseña" else "No puedo escanear: escribir el código de vinculación")
        }
        Text("La contraseña no se guarda en el teléfono: el servidor le da a este teléfono su propio acceso, que puede quitar en Mi cuenta › App Android.",
            color = C.muted, style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
fun MainScreen(ctx: Ctx) {
    val top = ctx.stack.lastOrNull()
    BackHandler(enabled = top != null) { ctx.stack.removeAt(ctx.stack.lastIndex) }
    if (top != null) {
        when (top) {
            is Screen.Device -> DeviceScreen(ctx, top.id)
            is Screen.Version -> VersionScreen(ctx, top.id, top.title)
            is Screen.Change -> ChangeScreen(ctx, top.id)
        }
        return
    }
    val tabs = listOf("Panel" to Icons.Filled.Home, "Equipos" to Icons.AutoMirrored.Filled.List, "Alarmas" to Icons.Filled.Notifications,
        "Cambios" to Icons.Filled.Check, "Más" to Icons.Filled.Menu)
    Scaffold(bottomBar = {
        NavigationBar {
            tabs.forEachIndexed { i, (label, icon) ->
                NavigationBarItem(selected = ctx.tab == i, onClick = { ctx.tab = i }, icon = { Icon(icon, label) }, label = { Text(label) })
            }
        }
    }) { pad ->
        Box(Modifier.padding(pad).fillMaxSize()) {
            when (ctx.tab) {
                0 -> PanelTab(ctx)
                1 -> DevicesTab(ctx)
                2 -> AlarmsTab(ctx)
                3 -> ChangesTab(ctx)
                else -> MoreTab(ctx)
            }
        }
    }
}

@Composable
fun Bar(title: String, ctx: Ctx, back: Boolean = false, onRefresh: (() -> Unit)? = null) {
    TopAppBar(
        title = { Text(title, maxLines = 1) },
        navigationIcon = {
            if (back) IconButton(onClick = { if (ctx.stack.isNotEmpty()) ctx.stack.removeAt(ctx.stack.lastIndex) }) {
                Icon(Icons.AutoMirrored.Filled.ArrowBack, "Atrás")
            }
        },
        actions = { if (onRefresh != null) IconButton(onClick = onRefresh) { Icon(Icons.Filled.Refresh, "Actualizar") } },
    )
}

@Composable
fun MoreTab(ctx: Ctx) {
    val user = runCatching { JSONObject(ctx.store.user) }.getOrNull()
    var notify by remember { mutableStateOf(ctx.store.notify) }
    var confirmOut by remember { mutableStateOf(false) }
    Column(Modifier.fillMaxSize()) {
        Bar("Más", ctx)
        Column(Modifier.verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(user?.optString("username") ?: "", style = MaterialTheme.typography.titleLarge)
            Text("Rol: " + mapOf("admin" to "Administrador", "operator" to "Operador", "viewer" to "Lector").getOrDefault(ctx.role, ctx.role), color = C.muted)
            Text(ctx.store.url, fontFamily = FontFamily.Monospace, color = C.muted)
            Text("App " + BuildConfigInfo.version, color = C.muted, style = MaterialTheme.typography.bodySmall)
            OutlinedButton(onClick = { ctx.act.scan("Escanee la etiqueta QR del equipo") { ctx.openLabel(it) } }, modifier = Modifier.fillMaxWidth()) {
                Text("Escanear la etiqueta de un equipo")
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("Avisos en el teléfono")
                    Text("Alarmas graves y cambios por aprobar, cada 15 minutos.", color = C.muted, style = MaterialTheme.typography.bodySmall)
                }
                androidx.compose.material3.Switch(checked = notify, onCheckedChange = {
                    notify = it; ctx.store.notify = it; Notify.schedule(ctx.act, it)
                    if (it) ctx.act.askNotifications()
                })
            }
            OutlinedButton(onClick = {
                ctx.act.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(ctx.store.url)))
            }, modifier = Modifier.fillMaxWidth()) { Text("Abrir OpenNCCM en el navegador") }
            Text("Plantillas, reglas, firmware, credenciales, tareas programadas y la administración del servidor se editan en la web.",
                color = C.muted, style = MaterialTheme.typography.bodySmall)
            Button(onClick = { confirmOut = true }, modifier = Modifier.fillMaxWidth()) { Text("Desconectar este teléfono") }
        }
    }
    if (confirmOut) AlertDialog(
        onDismissRequest = { confirmOut = false },
        title = { Text("¿Desconectar este teléfono?") },
        text = { Text("Se quita su acceso en el servidor. Para volver a entrar hará falta vincularlo de nuevo.") },
        confirmButton = {
            Button(onClick = {
                confirmOut = false
                ctx.scope.launch {
                    runCatching { ctx.api.post("/api/mobile/logout") }
                    Notify.schedule(ctx.act, false)
                    ctx.store.logout(); ctx.connected = false
                }
            }) { Text("Desconectar") }
        },
        dismissButton = { TextButton(onClick = { confirmOut = false }) { Text("Cancelar") } },
    )
}
