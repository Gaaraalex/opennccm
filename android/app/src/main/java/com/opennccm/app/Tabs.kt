@file:OptIn(ExperimentalMaterial3Api::class)

package com.opennccm.app

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import org.json.JSONArray
import org.json.JSONObject

fun JSONArray.objs(): List<JSONObject> = (0 until length()).map { getJSONObject(it) }

fun ago(iso: String?): String {
    if (iso.isNullOrEmpty() || iso == "null") return "—"
    return runCatching {
        val t = java.time.LocalDateTime.parse(iso.take(19))
        val m = java.time.Duration.between(t, java.time.LocalDateTime.now()).toMinutes()
        when {
            m < 1 -> "ahora mismo"
            m < 60 -> "hace $m min"
            m < 60 * 24 -> "hace ${m / 60} h"
            else -> "hace ${m / 60 / 24} días"
        }
    }.getOrDefault(iso.take(16).replace('T', ' '))
}

@Composable
fun Dot(color: Color, size: Int = 10) = Box(Modifier.size(size.dp).background(color, CircleShape))

@Composable
fun Pill(text: String, color: Color) {
    Text(text, color = color, fontSize = 12.sp, fontWeight = FontWeight.SemiBold,
        modifier = Modifier.background(color.copy(alpha = 0.16f), RoundedCornerShape(99.dp)).padding(horizontal = 10.dp, vertical = 3.dp))
}

@Composable
fun Offline(cached: Boolean) {
    if (cached) Text("Sin conexión: se muestran los últimos datos guardados en el teléfono.", color = C.warn,
        style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(horizontal = 16.dp, vertical = 6.dp))
}

/** Carga una pantalla y guarda la respuesta cifrada en el teléfono para verla sin conexión. */
@Composable
fun <T> load(ctx: Ctx, key: String, path: String, parse: (String) -> T, tick: Int): Pair<T?, Boolean> {
    var data by remember(key) { mutableStateOf<T?>(ctx.store.cache(key)?.let { runCatching { parse(it) }.getOrNull() }) }
    var cached by remember(key) { mutableStateOf(false) }
    LaunchedEffect(key, tick) {
        try {
            val text = ctx.api.call("GET", path)
            data = parse(text)
            cached = false
            ctx.store.cache(key, text)
        } catch (e: UntrustedCert) {
            val retry: suspend () -> Unit = {}
            ctx.pendingCert = e to retry
        } catch (e: ApiError) {
            ctx.toast(e.message)
        } catch (e: Exception) {
            cached = data != null
            if (data == null) ctx.toast("Sin conexión con el servidor")
        }
    }
    return data to cached
}

@Composable
fun Kpi(label: String, value: String, sub: String, color: Color, modifier: Modifier = Modifier, onClick: () -> Unit = {}) {
    Card(modifier.clickable { onClick() }) {
        Column(Modifier.padding(14.dp)) {
            Text(label.uppercase(), color = C.muted, fontSize = 11.sp, fontWeight = FontWeight.SemiBold)
            Text(value, color = color, fontSize = 30.sp, fontWeight = FontWeight.Bold)
            Text(sub, color = C.muted, style = MaterialTheme.typography.bodySmall)
        }
    }
}

@Composable
fun PanelTab(ctx: Ctx) {
    var tick by remember { mutableStateOf(0) }
    val (s, cached) = load(ctx, "summary", "/api/mobile/summary", { JSONObject(it) }, tick)
    Column(Modifier.fillMaxSize()) {
        Bar("Panel", ctx, onRefresh = { tick++ })
        Offline(cached)
        if (s == null) return
        val d = s.getJSONObject("devices")
        val a = s.getJSONObject("alarms")
        Column(Modifier.verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Kpi("Equipos en línea", "${d.optInt("ok")}/${d.optInt("total")}",
                    if (d.optInt("error") > 0) "${d.optInt("error")} sin conexión" else "todos responden",
                    if (d.optInt("error") > 0) C.crit else C.ok, Modifier.weight(1f)) { ctx.tab = 1 }
                Kpi("Alarmas abiertas", a.optInt("open").toString(), "${a.optInt("critical")} críticas · ${a.optInt("major")} mayores",
                    if (a.optInt("critical") > 0) C.crit else if (a.optInt("open") > 0) C.warn else C.ok, Modifier.weight(1f)) { ctx.tab = 2 }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Kpi("Cambios por aprobar", s.optInt("changes_pending").toString(), "solicitudes pendientes",
                    if (s.optInt("changes_pending") > 0) C.warn else C.muted, Modifier.weight(1f)) { ctx.tab = 3 }
                Kpi("Difieren de la base", d.optInt("drift").toString(), "configuración cambiada",
                    if (d.optInt("drift") > 0) C.major else C.muted, Modifier.weight(1f)) { ctx.tab = 1 }
            }
            val down = s.optJSONArray("down")?.objs().orEmpty()
            if (down.isNotEmpty()) {
                Text("Sin conexión", style = MaterialTheme.typography.titleMedium)
                down.forEach { e ->
                    Card(Modifier.fillMaxWidth().clickable { ctx.open(Screen.Device(e.getInt("id"))) }) {
                        Column(Modifier.padding(12.dp)) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Dot(C.crit); Spacer(Modifier.width(8.dp))
                                Text(e.optString("name"), fontWeight = FontWeight.SemiBold)
                                Spacer(Modifier.width(8.dp)); Text(e.optString("host"), color = C.muted, fontFamily = FontFamily.Monospace, fontSize = 12.sp)
                            }
                            Text(e.optString("error").lines().firstOrNull().orEmpty(), color = C.muted, maxLines = 2,
                                overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodySmall)
                        }
                    }
                }
            }
        }
    }
}

@Composable
fun DevicesTab(ctx: Ctx) {
    var tick by remember { mutableStateOf(0) }
    var q by remember { mutableStateOf("") }
    val (list, cached) = load(ctx, "devices", "/api/mobile/devices", { JSONArray(it).objs() }, tick)
    Column(Modifier.fillMaxSize()) {
        Bar("Equipos", ctx, onRefresh = { tick++ })
        Row(Modifier.padding(horizontal = 16.dp), verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(q, { q = it }, label = { Text("Buscar equipo, IP o modelo") }, singleLine = true, modifier = Modifier.weight(1f))
            Spacer(Modifier.width(8.dp))
            OutlinedButton(onClick = { ctx.act.scan("Escanee la etiqueta QR del equipo") { ctx.openLabel(it) } }) { Text("QR") }
        }
        Offline(cached)
        val shown = list.orEmpty().filter {
            q.isBlank() || "${it.optString("name")} ${it.optString("host")} ${it.optString("model")} ${it.optString("location")}".contains(q, true)
        }
        LazyColumn {
            items(shown, key = { it.getInt("id") }) { d ->
                Row(Modifier.fillMaxWidth().clickable { ctx.open(Screen.Device(d.getInt("id"))) }.padding(horizontal = 16.dp, vertical = 12.dp),
                    verticalAlignment = Alignment.CenterVertically) {
                    Dot(statusColor(d.optString("status")))
                    Spacer(Modifier.width(12.dp))
                    Column(Modifier.weight(1f)) {
                        Text(d.optString("name"), fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        Text("${d.optString("platform")} · ${d.optString("host")}", color = C.muted, fontSize = 12.sp, maxLines = 1,
                            overflow = TextOverflow.Ellipsis)
                    }
                    if (d.optBoolean("drift")) Pill("difiere", C.major)
                }
                HorizontalDivider()
            }
        }
    }
}

@Composable
fun AlarmsTab(ctx: Ctx) {
    var tick by remember { mutableStateOf(0) }
    val (list, cached) = load(ctx, "events", "/api/mobile/events", { JSONArray(it).objs() }, tick)
    Column(Modifier.fillMaxSize()) {
        Bar("Alarmas", ctx, onRefresh = { tick++ })
        Offline(cached)
        if (list != null && list.isEmpty()) Text("No hay alarmas abiertas.", color = C.muted, modifier = Modifier.padding(16.dp))
        LazyColumn {
            items(list.orEmpty(), key = { it.getInt("id") }) { e ->
                Column(Modifier.fillMaxWidth().clickable { if (!e.isNull("device_id")) ctx.open(Screen.Device(e.getInt("device_id"))) }
                    .padding(horizontal = 16.dp, vertical = 12.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Dot(sevColor(e.optString("severity")))
                        Spacer(Modifier.width(8.dp))
                        Text(e.optString("category"), fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                        Text(ago(e.optString("at")), color = C.muted, fontSize = 12.sp)
                    }
                    Text(e.optString("message").lines().take(3).joinToString(" · "), color = C.muted, maxLines = 3,
                        overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodySmall)
                    if (ctx.canRun) TextButton(onClick = {
                        ctx.run {
                            if (ctx.act.confirmIdentity("Reconocer la alarma", e.optString("category"))) {
                                ctx.api.post("/api/mobile/events/${e.getInt("id")}/ack"); ctx.toast("Alarma reconocida"); tick++
                            }
                        }
                    }) { Text("Reconocer") }
                }
                HorizontalDivider()
            }
        }
    }
}

val CR_STATUS = mapOf("pending" to "Pendiente", "approved" to "Aprobada", "running" to "Ejecutándose", "done" to "Hecha",
    "partial" to "Parcial", "failed" to "Fallida", "rejected" to "Rechazada")

fun crColor(s: String) = when (s) {
    "pending" -> C.warn
    "done", "approved", "running" -> C.ok
    "failed", "rejected" -> C.crit
    else -> C.muted
}

@Composable
fun ChangesTab(ctx: Ctx) {
    var tick by remember { mutableStateOf(0) }
    val (list, cached) = load(ctx, "changes", "/api/mobile/changes", { JSONArray(it).objs() }, tick)
    Column(Modifier.fillMaxSize()) {
        Bar("Cambios", ctx, onRefresh = { tick++ })
        Offline(cached)
        LazyColumn {
            items(list.orEmpty(), key = { it.getInt("id") }) { c ->
                Column(Modifier.fillMaxWidth().clickable { ctx.open(Screen.Change(c.getInt("id"))) }.padding(horizontal = 16.dp, vertical = 12.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text("#${c.getInt("id")} " + c.optString("title"), fontWeight = FontWeight.SemiBold, maxLines = 2,
                            overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                        Pill(CR_STATUS[c.optString("status")] ?: c.optString("status"), crColor(c.optString("status")))
                    }
                    val devs = c.optJSONArray("devices")?.let { a -> (0 until a.length()).map { a.getString(it) } }.orEmpty()
                    Text("${c.optString("requested_by")} · ${ago(c.optString("at"))} · ${devs.joinToString(", ")}", color = C.muted,
                        fontSize = 12.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
                HorizontalDivider()
            }
        }
    }
}

@Composable
fun ChangeScreen(ctx: Ctx, id: Int) {
    var tick by remember { mutableStateOf(0) }
    var reject by remember { mutableStateOf(false) }
    var comment by remember { mutableStateOf("") }
    val (c, _) = load(ctx, "change$id", "/api/mobile/changes/$id", { JSONObject(it) }, tick)
    fun review(decision: String, why: String) {
        ctx.run {
            val ok = ctx.act.confirmIdentity(if (decision == "approve") "Aprobar el cambio #$id" else "Rechazar el cambio #$id", c?.optString("title") ?: "")
            if (!ok) return@run
            try {
                val r = ctx.api.post("/api/mobile/changes/$id/review", JSONObject().put("decision", decision).put("comment", why))
                ctx.toast(r.optString("detail")); tick++
            } catch (e: ApiError) {
                ctx.toast(e.message)
            }
        }
    }
    Column(Modifier.fillMaxSize()) {
        Bar("Cambio #$id", ctx, back = true, onRefresh = { tick++ })
        if (c == null) return
        Column(Modifier.verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(c.optString("title"), style = MaterialTheme.typography.titleMedium)
            Row(verticalAlignment = Alignment.CenterVertically) {
                Pill(CR_STATUS[c.optString("status")] ?: c.optString("status"), crColor(c.optString("status")))
                Spacer(Modifier.width(8.dp))
                Text("pedida por ${c.optString("requested_by")} · ${ago(c.optString("at"))}", color = C.muted, fontSize = 12.sp)
            }
            val devs = c.optJSONArray("devices")?.let { a -> (0 until a.length()).map { a.getString(it) } }.orEmpty()
            Text("Equipos: " + devs.joinToString(", "))
            if (c.optString("comment").isNotEmpty() && c.optString("comment") != "null") Text("Comentario: " + c.optString("comment"), color = C.muted)
            Text("Comandos", fontWeight = FontWeight.SemiBold)
            SelectionContainer {
                Text(c.optString("body"), fontFamily = FontFamily.Monospace, fontSize = 12.sp,
                    modifier = Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(10.dp))
                        .horizontalScroll(rememberScrollState()).padding(10.dp))
            }
            if (c.optString("result").isNotBlank() && c.optString("result") != "null") {
                Text("Resultado", fontWeight = FontWeight.SemiBold)
                Text(c.optString("result"), fontFamily = FontFamily.Monospace, fontSize = 11.sp,
                    modifier = Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(10.dp)).padding(10.dp))
            }
            if (c.optString("status") == "pending" && ctx.isAdmin) {
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    Button(onClick = { review("approve", "") }, modifier = Modifier.weight(1f)) { Text("Aprobar") }
                    OutlinedButton(onClick = { reject = true }, modifier = Modifier.weight(1f)) { Text("Rechazar") }
                }
                Text("Se pide la huella antes de aprobar o rechazar.", color = C.muted, style = MaterialTheme.typography.bodySmall)
            }
        }
    }
    if (reject) AlertDialog(
        onDismissRequest = { reject = false },
        title = { Text("Rechazar el cambio") },
        text = { OutlinedTextField(comment, { comment = it }, label = { Text("Por qué se rechaza") }) },
        confirmButton = { Button(onClick = { reject = false; review("reject", comment) }, enabled = comment.isNotBlank()) { Text("Rechazar") } },
        dismissButton = { TextButton(onClick = { reject = false }) { Text("Cancelar") } },
    )
}

@Composable
fun VersionScreen(ctx: Ctx, id: Int, title: String) {
    val (v, _) = load(ctx, "ver$id", "/api/mobile/versions/$id", { JSONObject(it) }, 0)
    Column(Modifier.fillMaxSize()) {
        Bar(title, ctx, back = true)
        if (v == null) return
        SelectionContainer {
            Text(v.optString("text"), fontFamily = FontFamily.Monospace, fontSize = 11.sp,
                modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).horizontalScroll(rememberScrollState()).padding(12.dp))
        }
    }
}
