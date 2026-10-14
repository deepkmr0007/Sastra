package com.sastra.app.ui

import android.Manifest
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.sastra.app.ui.screens.*
import com.sastra.app.ui.theme.Bad
import com.sastra.app.ui.theme.Ok
import com.sastra.app.ui.theme.Warn
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SastraRoot(vm: AppViewModel) {
    val drawer = rememberDrawerState(DrawerValue.Closed)
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }

    val notifPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { }
    LaunchedEffect(Unit) {
        if (Build.VERSION.SDK_INT >= 33) notifPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        vm.go(vm.screen)
    }
    LaunchedEffect(vm.message) {
        vm.message?.let { snackbar.showSnackbar(it); vm.message = null }
    }

    ModalNavigationDrawer(
        drawerState = drawer,
        drawerContent = {
            ModalDrawerSheet {
                Text("SASTRA", style = MaterialTheme.typography.titleLarge, modifier = Modifier.padding(20.dp))
                Screen.entries.forEach { s ->
                    val badge = when (s) {
                        Screen.APPROVALS -> vm.status?.optInt("pending_approvals", 0) ?: 0
                        Screen.SECURITY -> vm.status?.optInt("security_alerts", 0) ?: 0
                        else -> 0
                    }
                    NavigationDrawerItem(
                        label = { Text(s.title) },
                        selected = vm.screen == s,
                        badge = { if (badge > 0) Pill(badge.toString(), Warn) },
                        onClick = { vm.go(s); scope.launch { drawer.close() } },
                        modifier = Modifier.padding(horizontal = 12.dp),
                    )
                }
            }
        },
    ) {
        Scaffold(
            topBar = {
                TopAppBar(
                    title = { Text(vm.screen.title) },
                    navigationIcon = {
                        IconButton(onClick = { scope.launch { drawer.open() } }) { Icon(Icons.Default.Menu, contentDescription = "Menu") }
                    },
                    actions = {
                        SystemStatusPill(vm)
                        IconButton(onClick = { vm.go(vm.screen) }) { Icon(Icons.Default.Refresh, contentDescription = "Refresh") }
                    },
                )
            },
            snackbarHost = { SnackbarHost(snackbar) },
        ) { pad ->
            Column(Modifier.padding(pad).fillMaxSize()) {
                if (vm.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
                when (vm.screen) {
                    Screen.HOME -> HomeScreen(vm)
                    Screen.CHAT -> ChatScreen(vm)
                    Screen.TASKS -> TasksScreen(vm)
                    Screen.RESEARCH -> ResearchScreen(vm)
                    Screen.PROJECTS -> ProjectsScreen(vm)
                    Screen.MEMORY -> MemoryScreen(vm)
                    Screen.FILES -> FilesScreen(vm)
                    Screen.APPROVALS -> ApprovalsScreen(vm)
                    Screen.MODELS -> ModelsScreen(vm)
                    Screen.SECURITY -> SecurityScreen(vm)
                    Screen.SETTINGS -> SettingsScreen(vm)
                }
            }
        }
    }
}

/** Always tells the truth about where answers come from right now. */
@Composable
fun SystemStatusPill(vm: AppViewModel) {
    val s = vm.status
    val (text, color) = when {
        !vm.online -> (if (vm.repo.localModel().configured) "Offline · on-device" else "Offline") to Warn
        s == null -> "Not signed in" to Warn
        s.optBoolean("agents_paused") -> "Paused" to Bad
        s.optInt("pending_approvals") > 0 -> "Waiting for approval" to Warn
        s.optBoolean("executing_code") -> "Running code" to Ok
        s.optBoolean("browsing") -> "Browsing" to Ok
        s.optString("model_state") == "offline" -> "Server: no model" to Warn
        s.optString("model_state") == "local_model" -> "Local model" to Ok
        else -> "Cloud model" to Ok
    }
    Pill(text, color)
}
