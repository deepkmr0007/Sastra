package com.sastra.app

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import com.sastra.app.data.LocalDb
import com.sastra.app.data.Repository
import com.sastra.app.data.SecureStore
import com.sastra.app.work.SyncWorker
import java.util.concurrent.TimeUnit

class SastraApp : Application() {
    lateinit var repo: Repository
        private set

    override fun onCreate() {
        super.onCreate()
        repo = Repository(LocalDb(this), SecureStore(this))
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CH_TASKS, getString(R.string.channel_tasks), NotificationManager.IMPORTANCE_DEFAULT))
        nm.createNotificationChannel(NotificationChannel(CH_APPROVALS, getString(R.string.channel_approvals), NotificationManager.IMPORTANCE_HIGH))
        nm.createNotificationChannel(NotificationChannel(CH_SECURITY, getString(R.string.channel_security), NotificationManager.IMPORTANCE_HIGH))
        scheduleSync()
    }

    fun scheduleSync() {
        val req = PeriodicWorkRequestBuilder<SyncWorker>(15, TimeUnit.MINUTES)
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .build()
        WorkManager.getInstance(this).enqueueUniquePeriodicWork("sastra-sync", ExistingPeriodicWorkPolicy.KEEP, req)
    }

    companion object {
        const val CH_TASKS = "tasks"
        const val CH_APPROVALS = "approvals"
        const val CH_SECURITY = "security"
    }
}
