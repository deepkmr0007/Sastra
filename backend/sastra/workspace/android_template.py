"""Generates a minimal, buildable Jetpack Compose Android project inside a workspace,
together with a GitHub Actions workflow that builds the debug APK in the cloud
(Android SDK builds are not run inside the code sandbox)."""
from __future__ import annotations

import re

AGP = "8.10.1"
KOTLIN = "2.1.21"
COMPOSE_BOM = "2025.06.00"


def files(app_name: str, package: str) -> dict[str, str]:
    if not re.fullmatch(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+", package):
        raise ValueError("package must look like com.example.app")
    pkg_path = package.replace(".", "/")
    safe_name = re.sub(r"[^A-Za-z0-9 ]", "", app_name)[:40] or "App"
    return {
        "settings.gradle.kts": f'''pluginManagement {{
    repositories {{ google(); mavenCentral(); gradlePluginPortal() }}
}}
dependencyResolutionManagement {{
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {{ google(); mavenCentral() }}
}}
rootProject.name = "{safe_name}"
include(":app")
''',
        "build.gradle.kts": f'''plugins {{
    id("com.android.application") version "{AGP}" apply false
    id("org.jetbrains.kotlin.android") version "{KOTLIN}" apply false
    id("org.jetbrains.kotlin.plugin.compose") version "{KOTLIN}" apply false
}}
''',
        "gradle.properties": "org.gradle.jvmargs=-Xmx2g\nandroid.useAndroidX=true\nkotlin.code.style=official\n",
        "app/build.gradle.kts": f'''plugins {{
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}}
android {{
    namespace = "{package}"
    compileSdk = 35
    defaultConfig {{
        applicationId = "{package}"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "1.0"
    }}
    buildFeatures {{ compose = true }}
    compileOptions {{
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }}
    kotlinOptions {{ jvmTarget = "17" }}
}}
dependencies {{
    implementation(platform("androidx.compose:compose-bom:{COMPOSE_BOM}"))
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.activity:activity-compose:1.10.1")
    implementation("androidx.core:core-ktx:1.16.0")
}}
''',
        "app/src/main/AndroidManifest.xml": f'''<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">
    <application android:label="{safe_name}" android:allowBackup="false"
        android:theme="@android:style/Theme.Material.NoActionBar">
        <activity android:name=".MainActivity" android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>
''',
        f"app/src/main/java/{pkg_path}/MainActivity.kt": f'''package {package}

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

class MainActivity : ComponentActivity() {{
    override fun onCreate(savedInstanceState: Bundle?) {{
        super.onCreate(savedInstanceState)
        setContent {{
            MaterialTheme {{
                var count by remember {{ mutableIntStateOf(0) }}
                Surface(Modifier.fillMaxSize()) {{
                    Column(Modifier.padding(24.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {{
                        Text("{safe_name}", style = MaterialTheme.typography.headlineMedium)
                        Text("Tapped $count times")
                        Button(onClick = {{ count++ }}) {{ Text("Tap") }}
                    }}
                }}
            }}
        }}
    }}
}}
''',
        ".github/workflows/android.yml": '''name: Build APK
on: [push, workflow_dispatch]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-java@v4
        with: { distribution: temurin, java-version: "17" }
      - uses: gradle/actions/setup-gradle@v4
        with: { gradle-version: "8.14.3" }
      - run: gradle assembleDebug --no-daemon
      - uses: actions/upload-artifact@v4
        with: { name: app-debug-apk, path: app/build/outputs/apk/debug/*.apk }
''',
        "BUILD_APK.md": f"""# Building {safe_name}

1. Push this folder to a GitHub repository.
2. GitHub Actions runs **Build APK** automatically (or run it from the Actions tab).
3. Download `app-debug-apk` from the run's Artifacts and install it (allow "Install unknown apps").

Local alternative: install Android Studio, open this folder, press Run.
""",
    }
