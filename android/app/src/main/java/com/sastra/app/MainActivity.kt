package com.sastra.app

import android.os.Bundle
import android.view.WindowManager
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.biometric.BiometricManager.Authenticators.BIOMETRIC_WEAK
import androidx.biometric.BiometricManager.Authenticators.DEVICE_CREDENTIAL
import androidx.biometric.BiometricPrompt
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentActivity
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import com.sastra.app.ui.AppViewModel
import com.sastra.app.ui.SastraRoot
import com.sastra.app.ui.theme.SastraTheme

class MainActivity : FragmentActivity() {
    private var unlocked by mutableStateOf(false)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val app = application as SastraApp
        // Keep conversations out of screenshots and the recent-apps thumbnail.
        if (app.repo.store.getBool("secure_screen", true)) window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        unlocked = !app.repo.store.getBool("app_lock", false)
        setContent {
            SastraTheme {
                Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
                    if (unlocked) {
                        val vm: AppViewModel = viewModel(factory = viewModelFactory { initializer { AppViewModel(app.repo) } })
                        SastraRoot(vm)
                    } else {
                        Column(Modifier.fillMaxSize().padding(32.dp), verticalArrangement = Arrangement.Center,
                            horizontalAlignment = Alignment.CenterHorizontally) {
                            Text("SASTRA is locked", style = MaterialTheme.typography.headlineSmall)
                            Button(onClick = { authenticate() }, modifier = Modifier.padding(top = 16.dp)) { Text("Unlock") }
                        }
                    }
                }
            }
        }
    }

    override fun onStop() {
        super.onStop()
        if ((application as SastraApp).repo.store.getBool("app_lock", false)) unlocked = false
    }

    override fun onStart() {
        super.onStart()
        if (!unlocked) authenticate()
    }

    private fun authenticate() {
        val prompt = BiometricPrompt(this, ContextCompat.getMainExecutor(this), object : BiometricPrompt.AuthenticationCallback() {
            override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) {
                unlocked = true
            }

            override fun onAuthenticationError(errorCode: Int, errString: CharSequence) {
                // The lock can only be enforced if the phone itself has a screen lock or biometric enrolled.
                if (errorCode == BiometricPrompt.ERROR_NO_DEVICE_CREDENTIAL || errorCode == BiometricPrompt.ERROR_HW_NOT_PRESENT ||
                    errorCode == BiometricPrompt.ERROR_NO_BIOMETRICS) unlocked = true
            }
        })
        val info = BiometricPrompt.PromptInfo.Builder()
            .setTitle(getString(R.string.unlock_title))
            .setSubtitle(getString(R.string.unlock_subtitle))
            .setAllowedAuthenticators(BIOMETRIC_WEAK or DEVICE_CREDENTIAL)
            .build()
        try {
            prompt.authenticate(info)
        } catch (_: Exception) {
            unlocked = true // no lock screen configured on the device: nothing stronger to check against
        }
    }
}
