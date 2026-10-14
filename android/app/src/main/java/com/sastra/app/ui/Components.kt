package com.sastra.app.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.sastra.app.ui.theme.Bad
import com.sastra.app.ui.theme.Ok
import com.sastra.app.ui.theme.Warn
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

@Composable
fun Pill(text: String, color: Color = MaterialTheme.colorScheme.onSurfaceVariant) {
    Surface(shape = RoundedCornerShape(50), color = color.copy(alpha = 0.14f)) {
        Text(text, color = color, fontSize = 12.sp, modifier = Modifier.padding(horizontal = 10.dp, vertical = 3.dp))
    }
}

fun stateColor(state: String): Color = when (state) {
    "COMPLETED", "active", "ok" -> Ok
    "FAILED", "CANCELLED", "BLOCKED", "rejected", "critical", "high" -> Bad
    "AWAITING_APPROVAL", "NEEDS_USER_INPUT", "quarantined", "conflict", "pending_validation", "QUEUED_OFFLINE", "medium" -> Warn
    else -> Color(0xFF7C9CFF)
}

@Composable
fun Section(title: String? = null, modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    Card(modifier.fillMaxWidth().padding(vertical = 6.dp), colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            if (title != null) Text(title, fontWeight = FontWeight.SemiBold, style = MaterialTheme.typography.titleSmall)
            content()
        }
    }
}

@Composable
fun Muted(text: String) = Text(text, color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.bodySmall)

@Composable
fun Mono(text: String) = Text(text, fontFamily = FontFamily.Monospace, fontSize = 12.sp)

@Composable
fun Field(value: String, onChange: (String) -> Unit, label: String, modifier: Modifier = Modifier, singleLine: Boolean = true,
          password: Boolean = false, minLines: Int = 1) {
    OutlinedTextField(
        value = value, onValueChange = onChange, label = { Text(label) }, singleLine = singleLine, minLines = minLines,
        visualTransformation = if (password) androidx.compose.ui.text.input.PasswordVisualTransformation()
        else androidx.compose.ui.text.input.VisualTransformation.None,
        modifier = modifier.fillMaxWidth(),
    )
}

@Composable
fun ToggleRow(label: String, checked: Boolean, hint: String? = null, onChange: (Boolean) -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.weight(1f)) {
            Text(label)
            if (hint != null) Muted(hint)
        }
        Switch(checked = checked, onCheckedChange = onChange)
    }
}

/** A choice list without experimental APIs: a button that opens a DropdownMenu. */
@Composable
fun Choice(label: String, options: List<String>, selected: String, onSelect: (String) -> Unit) {
    var open by remember { mutableStateOf(false) }
    Box {
        OutlinedButton(onClick = { open = true }) { Text("$label: $selected") }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            options.forEach { o -> DropdownMenuItem(text = { Text(o) }, onClick = { open = false; onSelect(o) }) }
        }
    }
}

/** Two-step confirmation for destructive actions. */
@Composable
fun ConfirmButton(label: String, confirmText: String, danger: Boolean = true, onConfirm: () -> Unit) {
    var ask by remember { mutableStateOf(false) }
    Button(onClick = { ask = true }, colors = if (danger) ButtonDefaults.buttonColors(containerColor = Bad) else ButtonDefaults.buttonColors()) {
        Text(label)
    }
    if (ask) {
        AlertDialog(
            onDismissRequest = { ask = false },
            title = { Text(label) },
            text = { Text(confirmText) },
            confirmButton = { TextButton(onClick = { ask = false; onConfirm() }) { Text("Yes, continue") } },
            dismissButton = { TextButton(onClick = { ask = false }) { Text("Cancel") } },
        )
    }
}

fun fmtTime(epochSeconds: Double): String =
    if (epochSeconds <= 0) "—" else SimpleDateFormat("d MMM, HH:mm", Locale.getDefault()).format(Date((epochSeconds * 1000).toLong()))

fun fmtMillis(ms: Long): String = SimpleDateFormat("d MMM, HH:mm", Locale.getDefault()).format(Date(ms))
