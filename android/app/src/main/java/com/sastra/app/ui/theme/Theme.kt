package com.sastra.app.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

val Ok = Color(0xFF3ECF8E)
val Warn = Color(0xFFF5B84A)
val Bad = Color(0xFFFF6B6B)

private val Dark = darkColorScheme(
    primary = Color(0xFF7C9CFF), onPrimary = Color(0xFF0B0F17),
    secondary = Ok, background = Color(0xFF0B0F17), surface = Color(0xFF121826),
    surfaceVariant = Color(0xFF1A2232), onBackground = Color(0xFFE6EBF5), onSurface = Color(0xFFE6EBF5),
    onSurfaceVariant = Color(0xFF8B97AD), error = Bad, outline = Color(0xFF2A3650),
)

private val Light = lightColorScheme(
    primary = Color(0xFF3355DD), secondary = Color(0xFF138A5C), background = Color(0xFFF6F7FB),
    surface = Color.White, surfaceVariant = Color(0xFFEDF0F6), error = Color(0xFFC62828),
)

@Composable
fun SastraTheme(dark: Boolean = isSystemInDarkTheme(), content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = if (dark) Dark else Light, typography = Typography(), content = content)
}
