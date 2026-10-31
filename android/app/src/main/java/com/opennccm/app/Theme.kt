package com.opennccm.app

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

// colores «Señal» de la web
object C {
    val brand = Color(0xFF2347E8)
    val brandDark = Color(0xFF7C96FF)
    val signal = Color(0xFFC6F432)
    val ok = Color(0xFF4ADE80)
    val okLight = Color(0xFF1F9D55)
    val warn = Color(0xFFF5B83D)
    val crit = Color(0xFFFF6B81)
    val major = Color(0xFFFF8A4C)
    val muted = Color(0xFF93A0B8)
    val down = Color(0xFF5B6782)
    val downBody = Color(0xFF273046)
    val offBody = Color(0xFF2A2110)
    val hole = Color(0xFF0B0F17)
    val empty = Color(0xFF121826)
}

@Composable
fun NccmTheme(content: @Composable () -> Unit) {
    val dark = isSystemInDarkTheme()
    MaterialTheme(
        colorScheme = if (dark) darkColorScheme(
            primary = C.brandDark, onPrimary = Color(0xFF0B0F17), secondary = C.signal,
            background = Color(0xFF090C13), surface = Color(0xFF111723), surfaceVariant = Color(0xFF161D2B),
            onBackground = Color(0xFFE8EDF6), onSurface = Color(0xFFE8EDF6), onSurfaceVariant = Color(0xFFC3CBD9),
            error = C.crit,
        ) else lightColorScheme(
            primary = C.brand, onPrimary = Color.White, secondary = Color(0xFF3C5A00),
            background = Color(0xFFEEF1F5), surface = Color.White, surfaceVariant = Color(0xFFF4F6F9),
            error = Color(0xFFC8102E),
        ),
        content = content,
    )
}

fun sevColor(s: String): Color = when (s) {
    "critical" -> C.crit
    "major" -> C.major
    "warning", "minor" -> C.warn
    else -> C.muted
}

fun statusColor(s: String): Color = when (s) {
    "ok" -> C.ok
    "error" -> C.crit
    else -> C.warn
}
