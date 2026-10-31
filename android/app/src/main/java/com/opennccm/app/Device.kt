@file:OptIn(ExperimentalMaterial3Api::class)

package com.opennccm.app

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import org.json.JSONObject

val PORT_STATE = mapOf("up" to "Activo", "down" to "Sin enlace", "off" to "Apagado", "empty" to "Sin módulo", "on" to "Habilitado")

fun portColor(s: String) = when (s) {
    "up" -> C.ok
    "off" -> C.warn
    else -> C.down
}

/** Posición de cada puerto en el frente, con las mismas reglas que la web (filas, bloques y SFP según el modelo). */
data class Slot(val port: JSONObject, val x: Float, val y: Float, val w: Float, val flip: Boolean)

fun frontLayout(ports: List<JSONObject>, layout: JSONObject?): Pair<List<Slot>, Size> {
    val cw = 32f; val sw = 42f; val h = 28f; val gap = 6f; val gg = 12f; val sep = 16f; val y0 = 6f
    val rows = layout?.optInt("rows", 1) ?: 1
    val grp = (layout?.optInt("grp", 6) ?: 6).coerceAtLeast(1)
    val gaps = layout?.optJSONArray("gaps")?.let { a -> (0 until a.length()).map { a.getInt(it) } }.orEmpty()
    val sfpMode = layout?.optString("sfp", "row") ?: "row"
    val cu = ports.filter { it.optString("k") != "sfp" }
    val sf = ports.filter { it.optString("k") == "sfp" }
    val out = mutableListOf<Slot>()
    var width: Float
    val height: Float
    if (rows < 2) {
        var x = 0f
        cu.forEachIndexed { i, p -> if (i > 0 && i in gaps) x += gg; out += Slot(p, x, y0, cw, false); x += cw + gap }
        var sx = if (cu.isNotEmpty()) x - gap + sep else 0f
        sf.forEach { p -> out += Slot(p, sx, y0, sw, false); sx += sw + gap }
        width = maxOf(x - gap, sx - gap)
        height = y0 + h + 6f
    } else {
        val y1 = y0 + h + 8f
        fun cx(c: Int) = c * (cw + gap) + (c / grp) * gg
        cu.forEachIndexed { i, p -> val b = i % 2 == 1; out += Slot(p, cx(i / 2), if (b) y1 else y0, cw, b) }
        val cols = (cu.size + 1) / 2
        val cuW = if (cols > 0) cx(cols - 1) + cw else 0f
        val sx0 = if (cu.isNotEmpty()) cuW + sep else 0f
        width = cuW
        if (sfpMode == "pairs") {
            sf.forEachIndexed { i, p -> val b = i % 2 == 1; out += Slot(p, sx0 + (i / 2) * (sw + gap), if (b) y1 else y0, sw, false) }
            if (sf.isNotEmpty()) width = sx0 + ((sf.size + 1) / 2) * (sw + gap) - gap
        } else {
            val yy = if (sfpMode == "bottom") y1 else y0
            sf.forEachIndexed { i, p -> out += Slot(p, sx0 + i * (sw + gap), yy, sw, false) }
            if (sf.isNotEmpty()) width = sx0 + sf.size * (sw + gap) - gap
        }
        height = y1 + h + 6f
    }
    return out to Size(maxOf(width, 1f), height)
}

fun DrawScope.drawPort(s: Slot, d: Float, selected: Boolean) {
    val st = s.port.optString("s")
    val sfp = s.port.optString("k") == "sfp"
    val body = when (st) {
        "up" -> C.ok
        "off" -> C.offBody
        "empty" -> C.empty
        else -> C.downBody
    }
    val edge = when (st) {
        "up" -> C.ok
        "off" -> C.warn
        else -> C.down
    }
    val tl = Offset(s.x * d, s.y * d)
    val sz = Size(s.w * d, 28f * d)
    drawRoundRect(body, tl, sz, CornerRadius(4f * d))
    drawRoundRect(edge, tl, sz, CornerRadius(4f * d), style = Stroke(1.5f * d,
        pathEffect = if (st == "off") PathEffect.dashPathEffect(floatArrayOf(3f * d, 2f * d)) else null))
    if (sfp) {
        drawRoundRect(C.hole, Offset((s.x + 6) * d, (s.y + 7) * d), Size((s.w - 12) * d, 14f * d), CornerRadius(1.5f * d))
        if (st != "empty") {
            val lc = if (st == "up") Color(0xFF0F2E1D) else C.downBody
            drawRoundRect(lc, Offset((s.x + 9.5f) * d, (s.y + 8.5f) * d), Size(10f * d, 11f * d), CornerRadius(2.5f * d))
            drawRoundRect(lc, Offset((s.x + 22.5f) * d, (s.y + 8.5f) * d), Size(10f * d, 11f * d), CornerRadius(2.5f * d))
        }
    } else {
        // hueco del RJ45 (invertido en la fila de abajo)
        val top = if (s.flip) s.y + 5 else s.y + 6
        drawRect(C.hole, Offset((s.x + 7) * d, (if (s.flip) s.y + 5 else s.y + 10) * d), Size(18f * d, 13f * d))
        drawRect(C.hole, Offset((s.x + 12) * d, (if (s.flip) s.y + 18 else top) * d), Size(8f * d, 4f * d))
    }
    if (selected) drawRoundRect(Color(0xFF7C96FF), Offset(tl.x - 3 * d, tl.y - 3 * d), Size(sz.width + 6 * d, sz.height + 6 * d),
        CornerRadius(6f * d), style = Stroke(2f * d))
}

@Composable
fun PortsFront(ports: List<JSONObject>, layout: JSONObject?, sel: String?, onPick: (JSONObject) -> Unit) {
    val (slots, size) = remember(ports, layout) { frontLayout(ports, layout) }
    val d = LocalDensity.current.density
    Row(Modifier.horizontalScroll(rememberScrollState()).background(Color(0xFF070A10), RoundedCornerShape(12.dp)).padding(12.dp)) {
        Canvas(Modifier.size((size.width).dp, (size.height).dp).pointerInput(slots) {
            detectTapGestures { o ->
                slots.firstOrNull { o.x in it.x * d..(it.x + it.w) * d && o.y in it.y * d..(it.y + 28) * d }?.let { onPick(it.port) }
            }
        }) {
            slots.forEach { drawPort(it, d, it.port.optString("n") == sel) }
        }
    }
}

@Composable
fun RearView(rear: JSONObject?) {
    val psu = rear?.optJSONArray("psu")?.objs().orEmpty()
    val fan = rear?.optJSONArray("fan")?.objs().orEmpty()
    val fanless = rear?.optBoolean("fanless") == true
    if (psu.isEmpty() && fan.isEmpty()) {
        Text("El equipo no informó de sus fuentes ni de sus ventiladores en el respaldo.", color = C.muted, style = MaterialTheme.typography.bodySmall)
        return
    }
    @Composable
    fun item(x: JSONObject, kind: String) {
        val s = x.optString("s")
        val col = when (s) { "ok" -> C.ok; "fail" -> C.crit; else -> C.down }
        val label = when (kind) {
            "psu" -> mapOf("ok" to "Con corriente", "fail" to "Sin corriente o con fallo", "absent" to "No instalada")
            else -> mapOf("ok" to "Funcionando", "fail" to "Parado o con fallo", "absent" to "No instalado")
        }[s] ?: s
        Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            Dot(col, 12)
            Spacer(Modifier.width(10.dp))
            Column(Modifier.weight(1f)) {
                Text(x.optString("n"), fontWeight = FontWeight.SemiBold)
                if (x.optString("st").isNotBlank()) Text(x.optString("st"), color = C.muted, fontFamily = FontFamily.Monospace, fontSize = 11.sp,
                    maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            Pill(label + if (x.has("rpm")) " · ${x.optInt("rpm")} RPM" else "", col)
        }
    }
    if (psu.isNotEmpty()) { Text("Fuentes de alimentación", color = C.muted, fontSize = 12.sp); psu.forEach { item(it, "psu") } }
    if (fan.isNotEmpty()) { Text("Ventiladores", color = C.muted, fontSize = 12.sp); fan.forEach { item(it, "fan") } }
    else if (fanless) Text("Sin ventiladores (refrigeración pasiva)", color = C.muted, fontSize = 12.sp)
}

@Composable
fun DeviceScreen(ctx: Ctx, id: Int) {
    var tick by remember { mutableStateOf(0) }
    var busy by remember { mutableStateOf(false) }
    var view by remember { mutableStateOf("front") }
    var sel by remember { mutableStateOf<String?>(null) }
    val (d, cached) = load(ctx, "dev$id", "/api/mobile/devices/$id", { JSONObject(it) }, tick)
    Column(Modifier.fillMaxSize()) {
        Bar(d?.optString("name") ?: "Equipo", ctx, back = true, onRefresh = { tick++ })
        Offline(cached)
        if (d == null) return
        Column(Modifier.verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Pill(when (d.optString("status")) { "ok" -> "En línea"; "error" -> "Sin conexión"; else -> "Pendiente" }, statusColor(d.optString("status")))
                if (d.optBoolean("drift")) { Spacer(Modifier.width(8.dp)); Pill("Difiere de la base", C.major) }
            }
            Text("${d.optString("platform")} · ${d.optString("host")}", color = C.muted)
            Text("Modelo ${d.optString("model").takeIf { it != "null" && it.isNotBlank() } ?: "—"} · firmware ${d.optString("version").takeIf { it != "null" && it.isNotBlank() } ?: "—"} · respaldado ${ago(d.optString("last_backup"))}",
                color = C.muted, style = MaterialTheme.typography.bodySmall)
            if (d.optString("status") == "error" && d.optString("last_error") != "null")
                Text(d.optString("last_error").lines().firstOrNull().orEmpty(), color = C.crit, style = MaterialTheme.typography.bodySmall)
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                if (ctx.canRun) Button(onClick = {
                    ctx.run {
                        if (!ctx.act.confirmIdentity("Respaldar ${d.optString("name")}", "Se conecta al equipo y guarda su configuración")) return@run
                        busy = true
                        try {
                            val r = ctx.api.post("/api/mobile/devices/$id/backup")
                            ctx.toast(when (r.optString("result")) {
                                "changed" -> "Respaldado: hubo cambios"; "unchanged" -> "Respaldado: sin cambios"; "first" -> "Primer respaldo hecho"
                                else -> "No se pudo respaldar: " + r.optString("error").lines().firstOrNull().orEmpty()
                            })
                            tick++
                        } finally { busy = false }
                    }
                }, enabled = !busy) { Text(if (busy) "Respaldando…" else "Respaldar ahora") }
                if (d.optString("web_url").startsWith("http")) OutlinedButton(onClick = {
                    ctx.act.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(d.optString("web_url"))))
                }) { Text("Interfaz web") }
            }
            val pv = d.optJSONObject("ports")
            if (pv != null) {
                Card { Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        FilterChip(view == "front", { view = "front" }, { Text("Frente") })
                        val bad = listOf("psu", "fan").any { k -> pv.optJSONObject("rear")?.optJSONArray(k)?.objs().orEmpty().any { it.optString("s") != "ok" } }
                        FilterChip(view == "rear", { view = "rear" }, { Text(if (bad) "Atrás ●" else "Atrás") })
                    }
                    val items = pv.optJSONArray("items")?.objs().orEmpty()
                    if (view == "front") {
                        PortsFront(items, pv.optJSONObject("layout"), sel) { sel = it.optString("n") }
                        val up = items.count { it.optString("s") == "up" }
                        Text(if (pv.optBoolean("status")) "$up activos de ${items.size} · leído ${ago(pv.optString("at"))}"
                            else "${items.size} puertos · sin estado de enlace", color = C.muted, fontSize = 12.sp)
                        val p = items.firstOrNull { it.optString("n") == sel }
                        if (p != null) Column(Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(10.dp)).padding(10.dp)) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text(p.optString("n"), fontFamily = FontFamily.Monospace, fontWeight = FontWeight.SemiBold)
                                Spacer(Modifier.width(8.dp)); Pill(PORT_STATE[p.optString("s")] ?: p.optString("s"), portColor(p.optString("s")))
                            }
                            listOf("Tipo" to (if (p.optString("k") == "sfp") "Fibra" else "Cobre"), "Descripción" to p.optString("d"),
                                "Velocidad" to p.optString("sp"), "Dirección / VLAN" to p.optString("net"), "Vecino" to p.optString("nb"),
                                "Módulo" to p.optString("mod")).filter { it.second.isNotBlank() }.forEach { (k, v) ->
                                Text("$k: $v", style = MaterialTheme.typography.bodySmall)
                            }
                        }
                    } else RearView(pv.optJSONObject("rear"))
                } }
                if (view == "front") {
                    Text("Interfaces", style = MaterialTheme.typography.titleMedium)
                    pv.optJSONArray("items")?.objs().orEmpty().forEach { p ->
                        Row(Modifier.fillMaxWidth().clickable { sel = p.optString("n") }.padding(vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                            Dot(portColor(p.optString("s")))
                            Spacer(Modifier.width(10.dp))
                            Column(Modifier.weight(1f)) {
                                Text(p.optString("n") + "  " + (PORT_STATE[p.optString("s")] ?: ""), fontFamily = FontFamily.Monospace, fontSize = 13.sp)
                                val sub = listOf(if (p.optString("k") == "sfp") "Fibra" else "Cobre", p.optString("d")).filter { it.isNotBlank() }
                                Text(sub.joinToString(" · "), color = C.muted, fontSize = 12.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            }
                            Text(p.optString("sp").ifBlank { "—" }, fontFamily = FontFamily.Monospace, fontSize = 12.sp)
                        }
                        HorizontalDivider()
                    }
                }
            }
            val vers = d.optJSONArray("versions")?.objs().orEmpty()
            if (vers.isNotEmpty()) {
                Spacer(Modifier.height(4.dp))
                Text("Versiones", style = MaterialTheme.typography.titleMedium)
                vers.take(10).forEach { v ->
                    Row(Modifier.fillMaxWidth().clickable { ctx.open(Screen.Version(v.getInt("id"), "${d.optString("name")} · v${v.optInt("n")}")) }
                        .padding(vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text("v${v.optInt("n")}", fontWeight = FontWeight.SemiBold, modifier = Modifier.width(48.dp))
                        Column(Modifier.weight(1f)) {
                            Text("${v.optString("kind")} · ${v.optString("trigger")} · ${ago(v.optString("at"))}", fontSize = 13.sp)
                            Text("+${v.optInt("added")} −${v.optInt("removed")}", color = C.muted, fontSize = 12.sp)
                        }
                    }
                    HorizontalDivider()
                }
            }
            val ev = d.optJSONArray("events")?.objs().orEmpty()
            if (ev.isNotEmpty()) {
                Spacer(Modifier.height(4.dp))
                Text("Eventos", style = MaterialTheme.typography.titleMedium)
                ev.take(10).forEach { e ->
                    Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                        Dot(sevColor(e.optString("severity")))
                        Spacer(Modifier.width(10.dp))
                        Column(Modifier.weight(1f)) {
                            Text(e.optString("category"), fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                            Text(e.optString("message").lines().firstOrNull().orEmpty(), color = C.muted, fontSize = 12.sp, maxLines = 2,
                                overflow = TextOverflow.Ellipsis)
                        }
                        Text(ago(e.optString("at")), color = C.muted, fontSize = 11.sp)
                    }
                }
            }
        }
    }
}
