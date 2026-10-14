package com.sastra.app.ui.screens

import androidx.biometric.BiometricManager
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.sastra.app.data.objects
import com.sastra.app.ui.*
import com.sastra.app.ui.theme.Bad
import com.sastra.app.ui.theme.Ok
import com.sastra.app.ui.theme.Warn

private val MODES = listOf(
    "0 — Advisory: answers, research and drafts only",
    "1 — Assisted: safe, reversible local actions",
    "2 — Delegated: bounded multi-step tasks",
    "3 — Scheduled worker: approved recurring jobs",
)

@Composable
fun SecurityScreen(vm: AppViewModel) {
    val c = vm.controls
    LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        if (c == null) {
            item { Muted("Connect to your server to manage security. On this phone: Settings → app lock and wipe.") }
            return@LazyColumn
        }
        item {
            Section("Emergency") {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    ConfirmButton("Stop everything", "Pause all agents, stop workers, switch off internet and cloud models?") { vm.panic() }
                    OutlinedButton(onClick = { vm.resume() }) { Text("Resume") }
                }
            }
            Section("Autonomy") {
                val mode = c.optInt("autonomy_mode")
                MODES.forEachIndexed { i, label ->
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        RadioButton(selected = mode == i, onClick = { vm.setControl("autonomy_mode", i) })
                        Text(label)
                    }
                }
                Muted("Every mode uses the same security policy and approvals. Higher modes only allow more of the safe work to run unattended.")
            }
            Section("Switches") {
                ToggleRow("Agents paused", c.optBoolean("agents_paused")) { vm.setControl("agents_paused", it) }
                ToggleRow("Internet access", c.optBoolean("internet_enabled")) { vm.setControl("internet_enabled", it) }
                ToggleRow("Background workers", c.optBoolean("workers_enabled")) { vm.setControl("workers_enabled", it) }
                ToggleRow("Cloud models", c.optBoolean("cloud_models_enabled"), "Off = only self-hosted/on-device models") { vm.setControl("cloud_models_enabled", it) }
            }
            val p = vm.privacy
            if (p != null) {
                Section("Privacy dashboard (last 30 days)") {
                    Text("Stays local:", style = MaterialTheme.typography.labelLarge)
                    (0 until (p.optJSONArray("stays_local")?.length() ?: 0)).forEach { Muted("• " + p.getJSONArray("stays_local").getString(it)) }
                    Text("Left the server:", style = MaterialTheme.typography.labelLarge)
                    val flows = p.optJSONArray("leaves_device")?.objects().orEmpty()
                    if (flows.isEmpty()) Muted("Nothing.")
                    flows.forEach { f ->
                        Text("${f.optString("provider")} · ${f.optString("data_class")} · ${f.optInt("events")} events · ${f.optLong("bytes_out")} bytes")
                        Muted(f.optString("retention_note"))
                    }
                    Pill(if (p.optBoolean("encryption_at_rest")) "Encryption at rest: on" else "Encryption at rest: OFF", if (p.optBoolean("encryption_at_rest")) Ok else Warn)
                    var days by remember { mutableStateOf(p.optInt("retention_days").toString()) }
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedTextField(days, { days = it }, label = { Text("Keep chats (days, 0 = forever)") }, modifier = Modifier.weight(1f), singleLine = true)
                        Button(onClick = { vm.setRetention(days.toIntOrNull() ?: 90) }) { Text("Set") }
                    }
                    OutlinedButton(onClick = { vm.backupNow() }) { Text("Encrypted backup now") }
                }
            }
            Section("Signed-in devices") {
                vm.sessions.forEach { s ->
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text(s.optString("device_name"))
                            Muted("last seen " + fmtTime(s.optDouble("last_seen_at")) + if (!s.isNull("revoked_at")) " · revoked" else "")
                        }
                        if (s.isNull("revoked_at")) TextButton(onClick = { vm.revokeSession(s.optString("id")) }) { Text("Revoke", color = Bad) }
                    }
                }
            }
            Text("Security alerts", style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(top = 8.dp))
        }
        items(vm.events.take(30)) { e ->
            Section {
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) { Pill(e.optString("severity"), stateColor(e.optString("severity"))); Pill(e.optString("kind")) }
                Text(e.optString("detail"))
                Muted(fmtTime(e.optDouble("ts")))
                if (e.optInt("acknowledged") == 0) TextButton(onClick = { vm.ackEvent(e.optString("id")) }) { Text("Acknowledge") }
            }
        }
        item {
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 8.dp)) {
                Text("Audit log", style = MaterialTheme.typography.titleSmall, modifier = Modifier.weight(1f))
                when (vm.auditIntact) {
                    true -> Pill("hash chain intact", Ok)
                    false -> Pill("TAMPERING DETECTED", Bad)
                    null -> {}
                }
            }
        }
        items(vm.audit) { a ->
            Column(Modifier.padding(vertical = 3.dp)) {
                Text("${a.optString("action")}  ${a.optString("target").takeIf { it != "null" } ?: ""}", style = MaterialTheme.typography.bodySmall)
                Muted(fmtTime(a.optDouble("ts")) + " · " + a.optString("actor"))
            }
        }
        item { Spacer(Modifier.height(24.dp)) }
    }
}

@Composable
fun SettingsScreen(vm: AppViewModel) {
    val ctx = LocalContext.current
    val store = vm.repo.store
    var url by remember { mutableStateOf(vm.repo.serverUrl.ifBlank { "https://" }) }
    var user by remember { mutableStateOf(vm.repo.username) }
    var pw by remember { mutableStateOf("") }
    var code by remember { mutableStateOf("") }
    var appLock by remember { mutableStateOf(store.getBool("app_lock", false)) }
    var secureScreen by remember { mutableStateOf(store.getBool("secure_screen", true)) }
    var voiceIn by remember { mutableStateOf(store.getBool("voice_in", false)) }
    var voiceOut by remember { mutableStateOf(store.getBool("voice_out", false)) }
    LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        item {
            Section("Server") {
                Field(url, { url = it }, "Your SASTRA server (https://…)")
                Button(onClick = { vm.saveServer(url) }) { Text("Save") }
                Pill(if (vm.online) "Reachable" else "Not reachable", if (vm.online) Ok else Warn)
            }
            Section(if (vm.repo.loggedIn) "Signed in as ${vm.repo.username}" else if (!vm.ownerExists) "First run: create the owner" else "Sign in") {
                if (vm.repo.loggedIn) {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(onClick = { vm.sync() }) { Text("Sync now") }
                        OutlinedButton(onClick = { vm.logout() }) { Text("Sign out") }
                    }
                    val secret = vm.totpSecret
                    if (secret == null) {
                        TextButton(onClick = { vm.beginTotp() }) { Text("Enable 2-step login (authenticator app)") }
                    } else {
                        Muted("Add this key to your authenticator app (Google Authenticator, Aegis…) as a time-based key, then type the 6-digit code:")
                        androidx.compose.foundation.text.selection.SelectionContainer { Mono(secret) }
                        var totp by remember { mutableStateOf("") }
                        Field(totp, { totp = it }, "6-digit code")
                        Button(onClick = { vm.confirmTotp(totp) }, enabled = totp.length >= 6) { Text("Confirm") }
                    }
                } else {
                    Field(user, { user = it }, "Username")
                    Field(pw, { pw = it }, "Password (10+ characters)", password = true)
                    Field(code, { code = it }, if (!vm.ownerExists) "Setup code from the server log" else "2-step code (if enabled)")
                    if (!vm.ownerExists) Button(onClick = { vm.createOwner(user, pw, code) }) { Text("Create owner & sign in") }
                    else Button(onClick = { vm.login(user, pw, code) }) { Text("Sign in") }
                }
            }
            Section("This phone") {
                val canLock = BiometricManager.from(ctx).canAuthenticate(
                    BiometricManager.Authenticators.BIOMETRIC_WEAK or BiometricManager.Authenticators.DEVICE_CREDENTIAL) == BiometricManager.BIOMETRIC_SUCCESS
                ToggleRow("App lock (fingerprint / PIN)", appLock, if (canLock) "Asks every time SASTRA is reopened" else "Set a screen lock on your phone first") {
                    if (canLock || !it) { appLock = it; store.putBool("app_lock", it) }
                }
                ToggleRow("Hide from screenshots & recents", secureScreen, "Takes effect on next launch") { secureScreen = it; store.putBool("secure_screen", it) }
                ToggleRow("Voice input", voiceIn, "Uses your phone's speech recognizer when you tap 🎤") { voiceIn = it; store.putBool("voice_in", it) }
                ToggleRow("Read answers aloud", voiceOut, "Text-to-speech, only when you tap Read") { voiceOut = it; store.putBool("voice_out", it) }
            }
            Section("Local activity") {
                vm.repo.db.auditLog(15).forEach { (ts, action, detail) -> Muted("${fmtMillis(ts)}  $action — $detail") }
            }
            Section("Danger zone") {
                ConfirmButton("Delete all SASTRA data on this phone",
                    "Deletes local chats, notes, queued tasks, the session token and settings from this phone. Your server is not touched.") { vm.wipePhone() }
            }
            Muted("SASTRA 1.0.0 · Android control center")
            Spacer(Modifier.height(24.dp))
        }
    }
}
